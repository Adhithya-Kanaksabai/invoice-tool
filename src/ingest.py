"""
ingest.py — normalize any input file (PDF or image) into a list of page images.

Everything downstream of this module is input-agnostic: it only ever sees a
list of base64-encoded PNG page images, never a file path or file type. See
design.md D2. Not a Worker itself — extract.py (the Extraction Worker) calls
this directly, since there's no retry/validation decision to make here (FR1/FR2).
"""

from __future__ import annotations

import base64
import glob
import io
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_DIMENSION = 2048  # resize cap so page images stay a reasonable upload size


@dataclass
class PageImage:
    index: int  # 0-based page number
    image: Image.Image  # PIL image, RGB
    b64_png: str  # base64-encoded PNG, ready for the vision API


def _resize_if_needed(image: Image.Image) -> Image.Image:
    if max(image.size) <= MAX_DIMENSION:
        return image
    scale = MAX_DIMENSION / max(image.size)
    new_size = (int(image.width * scale), int(image.height * scale))
    return image.resize(new_size, Image.LANCZOS)


def _encode_png_b64(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _find_poppler_path() -> str | None:
    """
    pdf2image needs Poppler's binaries on PATH. Falls back to the winget
    install location if PATH hasn't picked it up yet in the current shell
    (Windows requires a shell restart after a PATH-modifying install).
    """
    if shutil.which("pdftoppm"):
        return None  # already on PATH, let pdf2image find it itself

    candidates = glob.glob(
        str(
            Path.home()
            / "AppData/Local/Microsoft/WinGet/Packages"
            / "oschwartz10612.Poppler_Microsoft.Winget.Source_8wekyb3d8bbwe"
            / "poppler-*/Library/bin"
        )
    )
    return candidates[0] if candidates else None


def load_page_images(file_path: str | Path) -> list[PageImage]:
    """
    Load a PDF or image file and return a list of PageImage, one per page
    (a single-item list for image inputs). Resizes oversized pages down so
    the vision API isn't sent unnecessarily large uploads.
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext == ".pdf":
        from pdf2image import convert_from_path

        pages = convert_from_path(str(path), poppler_path=_find_poppler_path())
    elif ext in SUPPORTED_IMAGE_EXTS:
        pages = [Image.open(path)]
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    page_images: list[PageImage] = []
    for i, page in enumerate(pages):
        page = page.convert("RGB")
        page = _resize_if_needed(page)
        page_images.append(PageImage(index=i, image=page, b64_png=_encode_png_b64(page)))
    return page_images
