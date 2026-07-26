"""
fetch_cord_benchmark.py — one-off downloader for a small slice of CORD-v2
(the public "Consolidated Receipt Dataset"), NOT part of the production
pipeline. Companion to generate_sample_invoices.py / generate_hard_samples.py,
but pulling from a public benchmark instead of generating synthetic samples.

Why this exists: the 29-document hand-verified set (tests/sample_invoices,
tests/sample_receipts) is real and honest, but it's ours — nobody outside this
project can compare a number against it. CORD-v2 (naver-clova-ix/cord-v2 on
Hugging Face, CC-BY-4.0) is a standard, external, real-world receipt benchmark
that other extraction projects also get measured against — running against it
turns "98.8% on my own documents" into a number with an outside reference
point.

CORD's ground truth is deliberately NOT translated into a full Receipt-v1
record here. Its schema is messy by design (menu items are sometimes a dict,
sometimes a list; prices mix "." and "," as thousands separators with no
consistent convention; merchant name/date/tax are frequently just absent from
the annotation). Rather than paper over that with guessed defaults, this
script extracts the fields CORD provides reliably enough to score against:
the receipt's total and its line-item descriptions (on essentially every
receipt), plus the subtotal on the ~65% of receipts CORD actually annotates
one for. The ground truth key set is therefore PER-DOCUMENT — eval.py's
score_document only scores whatever keys are present in a given document's
ground truth, so a receipt CORD never annotated a subtotal for simply isn't
scored on subtotal rather than being penalised against a guessed one. This is
a legitimate, if narrower, benchmark rather than a padded one.

Usage: venv/Scripts/python.exe tests/fetch_cord_benchmark.py [--count 20]
Downloads ~225MB once (cached under tests/cord_benchmark/.cache/), then only
re-runs the fast local normalization on subsequent invocations.
"""

from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path

import requests
from PIL import Image

CORD_REPO = "naver-clova-ix/cord-v2"
CORD_LICENSE = "CC-BY-4.0"
CORD_SOURCE = f"https://huggingface.co/datasets/{CORD_REPO}"

OUT_DIR = Path(__file__).parent / "cord_benchmark"
IMAGES_DIR = OUT_DIR / "images"
GROUND_TRUTH_DIR = OUT_DIR / "ground_truth"
CACHE_DIR = OUT_DIR / ".cache"

_DIGITS_ONLY = re.compile(r"[^0-9]")


def _parse_cord_amount(raw) -> float | None:
    """
    CORD prices are strings like "60.000", "28,000", "@11000", "17,000",
    or occasionally a list of duplicate strings from an annotation quirk
    (e.g. ["46.636", "46.636"]). Indonesian retail receipts have no cents in
    practice, and CORD's own annotators used "." and "," interchangeably as
    THOUSANDS separators, not decimal points — there's no way to tell "." was
    meant as a decimal from the string alone, and treating it as one would
    silently shrink real totals by 1000x. Strip everything but digits instead
    of trying to guess which separator convention a given receipt used.
    """
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if raw is None:
        return None
    digits = _DIGITS_ONLY.sub("", str(raw))
    if not digits:
        return None
    return float(digits)


def _menu_items(gt_parse: dict) -> list[dict]:
    """CORD's `menu` field is a dict for a single-item receipt, a list for
    multi-item ones — normalize to always-a-list before reading names."""
    menu = gt_parse.get("menu")
    if menu is None:
        return []
    if isinstance(menu, dict):
        menu = [menu]
    return menu


def _build_ground_truth(gt_parse: dict) -> dict | None:
    """
    Returns a ground truth dict with only the fields this script can extract
    reliably, or None if even the total isn't parseable (the one field
    reliable enough across the set to make a document worth scoring at all).

    Field set is per-document, not fixed: `total` and item descriptions are on
    essentially every CORD receipt, but `subtotal` is only annotated on ~65% of
    them. It's included ONLY where CORD actually has it, rather than defaulting
    a missing subtotal to (say) the total — score_document only scores keys
    present in a document's own ground truth, so a receipt CORD never annotated
    a subtotal for simply isn't scored on subtotal, instead of being penalised
    against a value that was never in the source data. subtotal is worth adding
    where present because it's arithmetically meaningful (it must reconcile with
    the line items), unlike merchant/date which CORD annotates too
    inconsistently to trust as ground truth at all.
    """
    total = _parse_cord_amount(gt_parse.get("total", {}).get("total_price"))
    if total is None:
        return None

    items = []
    for item in _menu_items(gt_parse):
        name = item.get("nm")
        if name:
            items.append({"description": str(name).strip()})

    ground_truth: dict = {"total": total, "items": items}

    subtotal = _parse_cord_amount(gt_parse.get("sub_total", {}).get("subtotal_price"))
    if subtotal is not None:
        ground_truth["subtotal"] = subtotal

    return ground_truth


def _resolve_test_parquet_url() -> str:
    """The exact filename embeds a content hash Hugging Face assigns — look
    it up rather than hardcode it, so a future re-run doesn't silently 404
    if the dataset is ever re-uploaded."""
    api_url = f"https://huggingface.co/api/datasets/{CORD_REPO}"
    resp = requests.get(api_url, timeout=30)
    resp.raise_for_status()
    for sibling in resp.json().get("siblings", []):
        name = sibling["rfilename"]
        if name.startswith("data/test-") and name.endswith(".parquet"):
            return f"https://huggingface.co/datasets/{CORD_REPO}/resolve/main/{name}"
    raise RuntimeError(f"couldn't find a test-split parquet file in {CORD_REPO}")


def _download_test_parquet() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "cord_v2_test.parquet"
    if cache_path.exists():
        return cache_path
    url = _resolve_test_parquet_url()
    print(f"Downloading {url} (one-time, ~225MB)...")
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    cache_path.write_bytes(resp.content)
    return cache_path


def main(count: int) -> None:
    import pyarrow.parquet as pq

    parquet_path = _download_test_parquet()
    table = pq.read_table(parquet_path)

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)

    written = 0
    for row in table.to_pylist():
        if written >= count:
            break
        gt_parse = json.loads(row["ground_truth"])["gt_parse"]
        ground_truth = _build_ground_truth(gt_parse)
        if ground_truth is None:
            continue  # no parseable total -- not worth scoring, see docstring

        name = f"cord_{written:03d}"
        image = Image.open(io.BytesIO(row["image"]["bytes"])).convert("RGB")
        # CORD ships lossless PNGs (often 1-2MB each); re-encoding as JPEG
        # cuts that by ~10x with no measurable extraction-quality cost --
        # this project already treats JPEG as a normal input format (see
        # tests/sample_invoices/web_invoice_*.jpg), and a vision model reading
        # a receipt photo doesn't need pixel-perfect fidelity beyond what a
        # real phone camera would give it anyway.
        image.save(IMAGES_DIR / f"{name}.jpg", "JPEG", quality=85)
        (GROUND_TRUTH_DIR / f"{name}.json").write_text(json.dumps(ground_truth, indent=2))
        written += 1

    with_subtotal = sum(
        1
        for p in sorted(GROUND_TRUTH_DIR.glob("cord_*.json"))
        if "subtotal" in json.loads(p.read_text())
    )
    (OUT_DIR / "SOURCE.md").write_text(
        f"# CORD-v2 benchmark subset\n\n"
        f"{written} receipts from the CORD-v2 test split, source: {CORD_SOURCE} "
        f"(license: {CORD_LICENSE}).\n\n"
        "Ground truth here is a NARROW, honest subset of CORD's own annotations. "
        f"Every document is scored on `total` and line-item descriptions; the "
        f"{with_subtotal} of {written} that CORD also annotates a subtotal for are "
        "additionally scored on `subtotal`. merchant/date/tax are excluded because "
        "CORD annotates them too inconsistently to trust as ground truth -- see "
        "fetch_cord_benchmark.py's docstring. This is an external, public benchmark, "
        "run separately from and in addition to the hand-verified 29-document set "
        "under tests/sample_invoices and tests/sample_receipts, not a replacement "
        "for it.\n"
    )
    print(f"Wrote {written} CORD-v2 samples to {OUT_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()
    main(args.count)
