"""
generate_sample_invoices.py — test-data authoring script, NOT part of the
production pipeline. Requires reportlab (requirements-test.txt only).

Generates diverse invoice/receipt PDFs across multiple visual templates,
currencies, optional-field combinations, and item counts, then optionally
degrades a few to blurry/rotated/noisy images to genuinely exercise the
ambiguous/unreadable field_status path (every prior sample was a clean
digital PDF).

Deliberately does NOT generate documents with intentionally-wrong printed
arithmetic: ground truth would become ambiguous (is "correct" what's printed,
or what's mathematically consistent?). The wrong-arithmetic / Correction
Worker path is already exercised honestly by corrupting a parsed object
directly in tests, not by fabricating an inconsistent source document.

Run once to (re)generate; ground truth JSON files are then hand-verified by
opening each rendered file and checking it against tests/ground_truth/*.json
— the values below are what the script INTENDS to print, verification means
confirming the render actually matches, catching rendering bugs.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OUT_SAMPLES = Path(__file__).parent / "sample_invoices"
OUT_RECEIPTS = Path(__file__).parent / "sample_receipts"
OUT_TRUTH = Path(__file__).parent / "ground_truth"
OUT_TRUTH_RECEIPTS = Path(__file__).parent / "ground_truth_receipts"

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "Rs. "}
# INR uses "Rs." not the Unicode ₹ glyph — reportlab's base-14 PDF fonts use
# WinAnsiEncoding, which covers $/€/£ but not U+20B9, so ₹ rendered as a
# garbled tofu-box glyph. Caught by hand-verifying the actual rendered PDF,
# not trusting the generation script's own variables — exactly the point of
# hand-verification.


def _money(currency: str, amount: float) -> str:
    return f"{CURRENCY_SYMBOLS.get(currency, currency + ' ')}{amount:,.2f}"


def render_invoice_modern(data: dict, out_path: Path) -> None:
    c = canvas.Canvas(str(out_path), pagesize=letter)
    width, height = letter
    cur = data["currency"]

    c.setFont("Helvetica-Bold", 20)
    c.drawString(40, height - 60, data["vendor_name"])
    c.setFont("Helvetica", 24)
    c.drawRightString(width - 40, height - 60, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawRightString(width - 40, height - 78, f"# {data['invoice_number']}")

    y = height - 120
    c.setFont("Helvetica", 9)
    c.drawString(40, y, "Bill To:")
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y - 14, data["customer_name"])

    c.setFont("Helvetica", 9)
    c.drawRightString(width - 40, y, f"Date: {data['invoice_date']}")
    if data.get("due_date"):
        c.drawRightString(width - 40, y - 14, f"Due: {data['due_date']}")

    y -= 60
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y, "Item")
    c.drawString(300, y, "Qty")
    c.drawString(360, y, "Rate")
    c.drawString(440, y, "Amount")
    c.line(40, y - 4, width - 40, y - 4)

    y -= 20
    c.setFont("Helvetica", 9)
    for item in data["line_items"]:
        c.drawString(40, y, item["description"])
        c.drawString(300, y, str(item["quantity"]))
        c.drawString(360, y, _money(cur, item["unit_price"]))
        c.drawString(440, y, _money(cur, item["amount"]))
        y -= 18

    y -= 20
    c.line(300, y, width - 40, y)
    y -= 16
    for label, key in [
        ("Subtotal", "subtotal"),
        ("Discount", "discount"),
        ("Shipping", "shipping"),
        ("Tax", "tax"),
    ]:
        if data.get(key) is not None:
            c.drawString(360, y, f"{label}:")
            c.drawString(440, y, _money(cur, data[key]))
            y -= 16
    c.setFont("Helvetica-Bold", 10)
    c.drawString(360, y, "Total:")
    c.drawString(440, y, _money(cur, data["total"]))

    c.save()


def render_invoice_ruled(data: dict, out_path: Path) -> None:
    c = canvas.Canvas(str(out_path), pagesize=letter)
    width, height = letter
    cur = data["currency"]

    c.rect(30, height - 90, width - 60, 60)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, height - 55, f"{data['vendor_name']} — INVOICE #{data['invoice_number']}")
    c.setFont("Helvetica", 9)
    c.drawString(40, height - 75, f"Customer: {data['customer_name']}")

    y = height - 110
    c.setFont("Helvetica", 9)
    c.drawString(40, y, f"Invoice date: {data['invoice_date']}")
    if data.get("due_date"):
        c.drawString(250, y, f"Due date: {data['due_date']}")

    y -= 30
    table_top = y
    col_x = [40, 280, 340, 420, width - 40]
    c.setFont("Helvetica-Bold", 9)
    headers = ["Description", "Qty", "Unit", "Amount"]
    for i, h in enumerate(headers):
        c.drawString(col_x[i] + 4, y - 12, h)
    y -= 20
    c.setFont("Helvetica", 9)
    for item in data["line_items"]:
        row = [
            item["description"],
            str(item["quantity"]),
            _money(cur, item["unit_price"]),
            _money(cur, item["amount"]),
        ]
        for i, val in enumerate(row):
            c.drawString(col_x[i] + 4, y - 12, val)
        y -= 20
    table_bottom = y - 4
    for x in col_x:
        c.line(x, table_top, x, table_bottom)
    c.line(col_x[0], table_top, col_x[-1], table_top)
    c.line(col_x[0], table_bottom, col_x[-1], table_bottom)

    y = table_bottom - 20
    for label, key in [
        ("Subtotal", "subtotal"),
        ("Discount", "discount"),
        ("Shipping", "shipping"),
        ("Tax", "tax"),
    ]:
        if data.get(key) is not None:
            c.drawRightString(width - 120, y, f"{label}:")
            c.drawRightString(width - 40, y, _money(cur, data[key]))
            y -= 16
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(width - 120, y, "TOTAL:")
    c.drawRightString(width - 40, y, _money(cur, data["total"]))

    c.save()


def render_invoice_vat(data: dict, out_path: Path) -> None:
    c = canvas.Canvas(str(out_path), pagesize=letter)
    width, height = letter
    cur = data["currency"]

    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, height - 50, data["vendor_name"])
    c.setFont("Helvetica", 8)
    c.drawString(40, height - 64, "Tax Invoice")
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 40, height - 50, f"Invoice No: {data['invoice_number']}")
    c.drawRightString(width - 40, height - 64, f"Date: {data['invoice_date']}")
    if data.get("due_date"):
        c.drawRightString(width - 40, height - 78, f"Payment Due: {data['due_date']}")
    c.drawString(40, height - 90, f"Billed to: {data['customer_name']}")

    y = height - 130
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y, "Description")
    c.drawString(320, y, "Qty")
    c.drawString(380, y, "Rate")
    c.drawString(460, y, "Amount")
    y -= 16
    c.setFont("Helvetica", 9)
    for item in data["line_items"]:
        c.drawString(40, y, item["description"])
        c.drawString(320, y, str(item["quantity"]))
        c.drawString(380, y, _money(cur, item["unit_price"]))
        c.drawString(460, y, _money(cur, item["amount"]))
        y -= 16

    y -= 20
    c.drawRightString(width - 120, y, "Subtotal:")
    c.drawRightString(width - 40, y, _money(cur, data["subtotal"]))
    if data.get("tax") is not None:
        y -= 16
        c.drawRightString(width - 120, y, "VAT:")
        c.drawRightString(width - 40, y, _money(cur, data["tax"]))
    y -= 20
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(width - 120, y, "Total Due:")
    c.drawRightString(width - 40, y, _money(cur, data["total"]))

    c.save()


def render_receipt(data: dict, out_path: Path) -> None:
    c = canvas.Canvas(str(out_path), pagesize=(320, 500))
    width, height = 320, 500
    cur = data["currency"]

    c.setFont("Helvetica-Bold", 12)
    c.drawCentredString(width / 2, height - 30, data["merchant_name"])
    c.setFont("Helvetica", 8)
    c.drawCentredString(width / 2, height - 44, f"{data['transaction_date']}")
    if data.get("transaction_id"):
        c.drawCentredString(width / 2, height - 56, f"Txn: {data['transaction_id']}")
    if data.get("payment_method"):
        c.drawCentredString(width / 2, height - 68, f"Paid via {data['payment_method']}")

    y = height - 100
    c.line(20, y, width - 20, y)
    y -= 16
    c.setFont("Helvetica", 8)
    for item in data["items"]:
        c.drawString(20, y, f"{item['description']} x{item['quantity']}")
        c.drawRightString(width - 20, y, _money(cur, item["amount"]))
        y -= 14

    y -= 6
    c.line(20, y, width - 20, y)
    y -= 16
    for label, key in [("Subtotal", "subtotal"), ("Tax", "tax"), ("Tip", "tip")]:
        if data.get(key) is not None:
            c.drawString(20, y, label)
            c.drawRightString(width - 20, y, _money(cur, data[key]))
            y -= 14
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20, y, "TOTAL")
    c.drawRightString(width - 20, y, _money(cur, data["total"]))

    c.save()


def _find_poppler_path() -> str | None:
    """Same fallback as src/ingest.py::_find_poppler_path — duplicated here
    rather than imported, to keep this test-data script self-contained."""
    import glob
    import shutil

    if shutil.which("pdftoppm"):
        return None
    candidates = glob.glob(
        str(
            Path.home()
            / "AppData/Local/Microsoft/WinGet/Packages"
            / "oschwartz10612.Poppler_Microsoft.Winget.Source_8wekyb3d8bbwe"
            / "poppler-*/Library/bin"
        )
    )
    return candidates[0] if candidates else None


def degrade_to_image(
    pdf_path: Path, out_path: Path, blur: float = 0, rotate: float = 0, noise: bool = False
) -> None:
    """Render page 1 of a PDF to PNG and apply visual degradation, for the
    robustness subset — every other sample is a clean digital PDF, which
    never exercises field_status = ambiguous/unreadable in practice."""
    from pdf2image import convert_from_path

    pages = convert_from_path(str(pdf_path), poppler_path=_find_poppler_path())
    img = pages[0].convert("RGB")
    if blur:
        img = img.filter(ImageFilter.GaussianBlur(blur))
    if rotate:
        img = img.rotate(rotate, expand=True, fillcolor="white")
    if noise:
        import numpy as np

        arr = np.array(img).astype(int)
        noise_arr = np.random.randint(-25, 25, arr.shape)
        arr = np.clip(arr + noise_arr, 0, 255).astype("uint8")
        img = Image.fromarray(arr)
    img.save(out_path)


INVOICES = [
    dict(
        template="modern",
        vendor_name="Bright Software Co",
        customer_name="Dana Kim",
        invoice_number="INV-1001",
        invoice_date="2024-02-01",
        due_date="2024-03-01",
        currency="USD",
        line_items=[
            dict(description="Annual SaaS License", quantity=1, unit_price=1200.00, amount=1200.00)
        ],
        subtotal=1200.00,
        tax=96.00,
        total=1296.00,
    ),
    dict(
        template="modern",
        vendor_name="NordicTools GmbH",
        customer_name="Lars Eriksen",
        invoice_number="INV-1002",
        invoice_date="2024-02-05",
        due_date=None,
        currency="EUR",
        line_items=[
            dict(description="Steel Hinges", quantity=50, unit_price=2.40, amount=120.00),
            dict(description="Wood Screws", quantity=200, unit_price=0.15, amount=30.00),
        ],
        subtotal=150.00,
        discount=15.00,
        shipping=8.50,
        total=143.50,
    ),
    dict(
        template="ruled",
        vendor_name="Thistle & Co",
        customer_name="Moira Campbell",
        invoice_number="INV-1003",
        invoice_date="2024-01-20",
        due_date="2024-02-19",
        currency="GBP",
        line_items=[
            dict(description="Wool Blanket", quantity=2, unit_price=45.00, amount=90.00),
            dict(description="Tartan Scarf", quantity=3, unit_price=22.00, amount=66.00),
            dict(description="Leather Gloves", quantity=1, unit_price=38.00, amount=38.00),
        ],
        subtotal=194.00,
        discount=19.40,
        shipping=12.00,
        tax=15.00,
        total=201.60,
    ),
    dict(
        template="ruled",
        vendor_name="Cascade Print Shop",
        customer_name="Renee Foster",
        invoice_number="INV-1004",
        invoice_date="2024-03-10",
        due_date=None,
        currency="USD",
        line_items=[
            dict(
                description="Business Card Printing (500ct)",
                quantity=1,
                unit_price=85.00,
                amount=85.00,
            )
        ],
        subtotal=85.00,
        total=85.00,
    ),
    dict(
        template="vat",
        vendor_name="Sunrise Textiles Pvt Ltd",
        customer_name="Amit Verma",
        invoice_number="INV-1005",
        invoice_date="2024-02-14",
        due_date="2024-03-15",
        currency="INR",
        line_items=[
            dict(description="Cotton Fabric (m)", quantity=20, unit_price=150.00, amount=3000.00),
            dict(description="Zippers", quantity=100, unit_price=8.00, amount=800.00),
            dict(description="Thread Spools", quantity=30, unit_price=25.00, amount=750.00),
            dict(description="Buttons (gross)", quantity=5, unit_price=60.00, amount=300.00),
        ],
        subtotal=4850.00,
        tax=873.00,
        total=5723.00,
    ),
    dict(
        template="modern",
        vendor_name="Bright Software Co",
        customer_name="Priya Nair",
        invoice_number="INV-1006",
        invoice_date="2024-04-10",
        due_date="2024-03-25",
        currency="USD",
        line_items=[
            dict(description="Consulting Hours", quantity=5, unit_price=150.00, amount=750.00)
        ],
        subtotal=750.00,
        tax=60.00,
        total=810.00,  # due_date BEFORE invoice_date on purpose (D6 date-order warning)
    ),
    dict(
        template="modern",
        vendor_name="Golden Gate Bakery",
        customer_name="Isabel Chen",
        invoice_number="INV-1008",
        invoice_date="2024-03-01",
        due_date=None,
        currency="USD",
        line_items=[
            dict(description="Sourdough Loaf", quantity=10, unit_price=6.50, amount=65.00),
            dict(description="Croissant (dozen)", quantity=3, unit_price=18.00, amount=54.00),
        ],
        subtotal=119.00,
        discount=11.90,
        total=107.10,
        degrade=dict(blur=2.5),
    ),
    dict(
        template="ruled",
        vendor_name="Peak Outdoor Gear",
        customer_name="Trevor Wallace",
        invoice_number="INV-1009",
        invoice_date="2024-02-20",
        due_date="2024-03-20",
        currency="USD",
        line_items=[
            dict(description="Hiking Backpack 40L", quantity=1, unit_price=129.99, amount=129.99)
        ],
        subtotal=129.99,
        shipping=9.99,
        total=139.98,
        degrade=dict(rotate=6),
    ),
    dict(
        template="vat",
        vendor_name="Lumiere Design Studio",
        customer_name="Claire Dubois",
        invoice_number="INV-1010",
        invoice_date="2024-01-15",
        due_date="2024-02-14",
        currency="EUR",
        line_items=[
            dict(description="Logo Design Package", quantity=1, unit_price=800.00, amount=800.00),
            dict(description="Business Card Design", quantity=1, unit_price=150.00, amount=150.00),
        ],
        subtotal=950.00,
        tax=190.00,
        total=1140.00,
        degrade=dict(noise=True),
    ),
]

RECEIPTS = [
    dict(
        merchant_name="Perk Coffee House",
        transaction_id="RCP-5521",
        transaction_date="2024-03-05",
        payment_method="Card",
        currency="USD",
        items=[
            dict(description="Cappuccino", quantity=1, unit_price=4.75, amount=4.75),
            dict(description="Blueberry Muffin", quantity=1, unit_price=3.25, amount=3.25),
        ],
        subtotal=8.00,
        tax=0.64,
        total=8.64,
    ),
    dict(
        merchant_name="The Rustic Fork",
        transaction_id="RCP-7788",
        transaction_date="2024-02-11",
        payment_method="Visa",
        currency="GBP",
        items=[
            dict(description="Grilled Salmon", quantity=1, unit_price=22.50, amount=22.50),
            dict(description="House Salad", quantity=1, unit_price=8.00, amount=8.00),
            dict(description="Sparkling Water", quantity=2, unit_price=3.00, amount=6.00),
        ],
        subtotal=36.50,
        tax=2.92,
        tip=5.48,
        total=44.90,
    ),
    dict(
        merchant_name="QuickMart",
        transaction_id=None,
        transaction_date="2024-03-20",
        payment_method=None,
        currency="USD",
        items=[dict(description="AA Batteries (4pk)", quantity=2, unit_price=5.99, amount=11.98)],
        subtotal=11.98,
        total=11.98,
        degrade=dict(blur=1.8),
    ),
]

INVOICE_TEMPLATES = {
    "modern": render_invoice_modern,
    "ruled": render_invoice_ruled,
    "vat": render_invoice_vat,
}


def _invoice_ground_truth(data: dict) -> dict:
    return {
        "vendor_name": data["vendor_name"],
        "customer_name": data["customer_name"],
        "invoice_number": data["invoice_number"],
        "invoice_date": data["invoice_date"],
        "due_date": data.get("due_date"),
        "currency": data["currency"],
        "line_items": data["line_items"],
        "subtotal": data["subtotal"],
        "discount": data.get("discount"),
        "shipping": data.get("shipping"),
        "tax": data.get("tax"),
        "total": data["total"],
    }


def _receipt_ground_truth(data: dict) -> dict:
    return {
        "merchant_name": data["merchant_name"],
        "transaction_id": data.get("transaction_id"),
        "transaction_date": data["transaction_date"],
        "payment_method": data.get("payment_method"),
        "currency": data["currency"],
        "items": data["items"],
        "subtotal": data["subtotal"],
        "tax": data.get("tax"),
        "tip": data.get("tip"),
        "total": data["total"],
    }


def main() -> None:
    random.seed(42)
    OUT_SAMPLES.mkdir(exist_ok=True)
    OUT_RECEIPTS.mkdir(exist_ok=True)
    OUT_TRUTH.mkdir(exist_ok=True)
    OUT_TRUTH_RECEIPTS.mkdir(exist_ok=True)

    for data in INVOICES:
        name = f"gen_invoice_{data['invoice_number']}"
        pdf_path = OUT_SAMPLES / f"{name}.pdf"
        INVOICE_TEMPLATES[data["template"]](data, pdf_path)

        if "degrade" in data:
            png_path = OUT_SAMPLES / f"{name}.png"
            degrade_to_image(pdf_path, png_path, **data["degrade"])
            pdf_path.unlink()  # the degraded image IS the sample; drop the clean PDF

        gt_path = OUT_TRUTH / f"{name}.json"
        gt_path.write_text(json.dumps(_invoice_ground_truth(data), indent=2))
        print(f"wrote {name}")

    for i, data in enumerate(RECEIPTS, start=1):
        name = f"gen_receipt_{i:02d}"
        pdf_path = OUT_RECEIPTS / f"{name}.pdf"
        render_receipt(data, pdf_path)

        if "degrade" in data:
            png_path = OUT_RECEIPTS / f"{name}.png"
            degrade_to_image(pdf_path, png_path, **data["degrade"])
            pdf_path.unlink()

        gt_path = OUT_TRUTH_RECEIPTS / f"{name}.json"
        gt_path.write_text(json.dumps(_receipt_ground_truth(data), indent=2))
        print(f"wrote {name}")


if __name__ == "__main__":
    main()
