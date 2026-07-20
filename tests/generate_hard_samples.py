"""
generate_hard_samples.py — v2 test-data authoring, NOT part of the production
pipeline. Requires reportlab + pdf2image + Pillow + numpy (requirements-test).

Companion to generate_sample_invoices.py. That script's degraded samples are
*mild* (blur 2.5, rotate 6) — a strong vision model reads through them fine, so
the v1 eval scored 100% and the validation/retry/confidence machinery never had
a real error to act on. This script adds a HARDER-degraded batch (heavy blur,
large rotation, downscale-to-low-res, JPEG artifacts, heavy noise, and
combinations) specifically to induce genuine extraction misreads — so the
OCR cross-check can actually disagree and confidence can actually drop.

Additive by design: writes only NEW invoice numbers (INV-20xx) and skips any
file that already exists, so it never churns the committed v1 samples. Ground
truth is the values this script INTENDS to print (same hand-verification
methodology as the original generator — the intended values are confirmed
against the rendered file).

Reuses the render_* functions from generate_sample_invoices.py rather than
duplicating them.
"""

from __future__ import annotations

import json
from pathlib import Path

from generate_sample_invoices import (
    INVOICE_TEMPLATES,
    OUT_SAMPLES,
    OUT_TRUTH,
    _find_poppler_path,
    _invoice_ground_truth,
)
from PIL import Image, ImageFilter


def hard_degrade(
    pdf_path: Path,
    out_path: Path,
    blur: float = 0,
    rotate: float = 0,
    noise: bool = False,
    downscale: float = 1.0,
    jpeg_quality: int | None = None,
) -> None:
    """Render page 1 of a PDF to an image and apply heavier, combinable
    degradations than the original generator's degrade_to_image:
    - downscale: shrink then re-enlarge to simulate a low-resolution scan
      (destroys fine digit detail — the main driver of number misreads).
    - jpeg_quality: round-trip through lossy JPEG to add compression artifacts.
    """
    from pdf2image import convert_from_path

    pages = convert_from_path(str(pdf_path), poppler_path=_find_poppler_path())
    img = pages[0].convert("RGB")

    if downscale and downscale != 1.0:
        w, h = img.size
        small = img.resize((max(1, int(w * downscale)), max(1, int(h * downscale))))
        img = small.resize((w, h))  # back up to original size, detail already lost

    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))

    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")

    if noise:
        import numpy as np

        arr = np.array(img).astype(int)
        noise_arr = np.random.randint(-40, 40, arr.shape)  # heavier than the original ±25
        arr = np.clip(arr + noise_arr, 0, 255).astype("uint8")
        img = Image.fromarray(arr)

    if jpeg_quality is not None:
        import io

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=jpeg_quality)
        buf.seek(0)
        img = Image.open(buf).convert("RGB")

    img.save(out_path)


# New, number-dense invoices with heavy/combined degradation. Number-dense +
# all-optional-fields-present maximises the chance a misread digit trips the
# arithmetic business rule (subtotal - discount + shipping + tax == total) or
# the line-items-sum rule — i.e. exactly the paths v1 never exercised.
HARD_INVOICES = [
    dict(
        template="modern",
        vendor_name="Ironclad Hardware LLC",
        customer_name="Marcus Webb",
        invoice_number="INV-2001",
        invoice_date="2024-05-02",
        due_date="2024-06-01",
        currency="USD",
        line_items=[
            dict(description="Hex Bolts M8 (box)", quantity=12, unit_price=7.45, amount=89.40),
            dict(description="Lock Washers (pack)", quantity=8, unit_price=3.20, amount=25.60),
            dict(description="Threadlocker 50ml", quantity=4, unit_price=11.75, amount=47.00),
        ],
        subtotal=162.00,
        discount=16.20,
        shipping=14.50,
        tax=12.96,
        total=173.26,
        degrade=dict(blur=4.5, downscale=0.5),
    ),
    dict(
        template="ruled",
        vendor_name="Delft Ceramics BV",
        customer_name="Sofie Visser",
        invoice_number="INV-2002",
        invoice_date="2024-04-18",
        due_date="2024-05-18",
        currency="EUR",
        line_items=[
            dict(description="Glazed Tiles (m2)", quantity=15, unit_price=42.00, amount=630.00),
            dict(description="Grout 5kg", quantity=6, unit_price=9.50, amount=57.00),
        ],
        subtotal=687.00,
        discount=34.35,
        shipping=22.00,
        tax=137.40,
        total=812.05,
        degrade=dict(rotate=15, noise=True),
    ),
    dict(
        template="vat",
        vendor_name="Kadam Auto Spares Pvt Ltd",
        customer_name="Ramesh Iyer",
        invoice_number="INV-2003",
        invoice_date="2024-03-27",
        due_date="2024-04-26",
        currency="INR",
        line_items=[
            dict(description="Brake Pads (set)", quantity=3, unit_price=1250.00, amount=3750.00),
            dict(description="Oil Filter", quantity=10, unit_price=180.00, amount=1800.00),
            dict(description="Wiper Blades (pair)", quantity=5, unit_price=420.00, amount=2100.00),
        ],
        subtotal=7650.00,
        tax=1377.00,
        total=9027.00,
        degrade=dict(blur=3.0, downscale=0.45, jpeg_quality=25),
    ),
    dict(
        template="modern",
        vendor_name="Harbour Marine Supplies",
        customer_name="Elena Popova",
        invoice_number="INV-2004",
        invoice_date="2024-05-20",
        due_date="2024-06-19",
        currency="GBP",
        line_items=[
            dict(description="Marine Rope 10mm (m)", quantity=50, unit_price=1.85, amount=92.50),
            dict(description="Stainless Shackle", quantity=8, unit_price=6.40, amount=51.20),
            dict(description="Fender Large", quantity=4, unit_price=23.00, amount=92.00),
        ],
        subtotal=235.70,
        discount=11.79,
        shipping=15.00,
        tax=47.78,
        total=286.69,
        degrade=dict(blur=2.5, noise=True, jpeg_quality=30),
    ),
    dict(
        template="ruled",
        vendor_name="Copperfield Print Works",
        customer_name="Nathan Brooks",
        invoice_number="INV-2005",
        invoice_date="2024-04-05",
        due_date="2024-05-05",
        currency="USD",
        line_items=[
            dict(description="Poster A2 Gloss", quantity=25, unit_price=4.60, amount=115.00),
            dict(description="Vinyl Banner 2m", quantity=3, unit_price=38.00, amount=114.00),
            dict(description="Lamination", quantity=25, unit_price=1.20, amount=30.00),
        ],
        subtotal=259.00,
        shipping=18.00,
        tax=22.16,
        total=299.16,
        degrade=dict(rotate=12, blur=3.0, downscale=0.6),
    ),
    dict(
        template="vat",
        vendor_name="Alpine Optics GmbH",
        customer_name="Johann Keller",
        invoice_number="INV-2006",
        invoice_date="2024-03-11",
        due_date="2024-04-10",
        currency="EUR",
        line_items=[
            dict(description="Binoculars 10x42", quantity=2, unit_price=189.00, amount=378.00),
            dict(description="Lens Cloth (pack)", quantity=6, unit_price=4.50, amount=27.00),
        ],
        subtotal=405.00,
        tax=81.00,
        total=486.00,
        degrade=dict(downscale=0.4, noise=True, jpeg_quality=20),
    ),
]


def main() -> None:
    OUT_SAMPLES.mkdir(exist_ok=True)
    OUT_TRUTH.mkdir(exist_ok=True)

    for data in HARD_INVOICES:
        name = f"gen_invoice_{data['invoice_number']}"
        pdf_path = OUT_SAMPLES / f"{name}.pdf"
        png_path = OUT_SAMPLES / f"{name}.png"
        gt_path = OUT_TRUTH / f"{name}.json"

        if png_path.exists() or gt_path.exists():
            print(f"skip {name} (already exists)")
            continue

        INVOICE_TEMPLATES[data["template"]](data, pdf_path)
        hard_degrade(pdf_path, png_path, **data["degrade"])
        pdf_path.unlink()  # the degraded image IS the sample

        gt_path.write_text(json.dumps(_invoice_ground_truth(data), indent=2))
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
