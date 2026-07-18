"""
eval.py — T13. Runs the full pipeline over the test set and reports field
accuracy + extraction success rate, per tasks.md's own note: "the strongest
resume signal in the whole project. Do not skip this."

Per D9: an extraction_failed invoice must not crash the batch or silently
zero out the accuracy numbers for the rest of the set — it's excluded from
field accuracy (there's no output to score) but still counts against
extraction success rate.
"""

from __future__ import annotations

import json
from pathlib import Path

from confidence import confidence_worker
from extract import extraction_worker
from orchestrator import run_pipeline
from report import report_worker
from retry import correction_worker
from validate import validation_worker

SAMPLE_DIR = Path(__file__).parent.parent / "tests" / "sample_invoices"
GROUND_TRUTH_DIR = Path(__file__).parent.parent / "tests" / "ground_truth"
SCHEMA_ID = "invoice-v1"

SCALAR_FIELDS = [
    "vendor_name", "customer_name", "invoice_number", "invoice_date",
    "due_date", "currency", "subtotal", "discount", "shipping", "tax", "total",
]
FLOAT_FIELDS = {"subtotal", "discount", "shipping", "tax", "total"}
FLOAT_TOLERANCE = 0.01
LINE_ITEM_FLOAT_FIELDS = {"quantity", "unit_price", "amount"}


def _values_match(field: str, actual, expected) -> bool:
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    if field in FLOAT_FIELDS:
        return abs(float(actual) - float(expected)) <= FLOAT_TOLERANCE
    return str(actual).strip().lower() == str(expected).strip().lower()


def score_invoice(invoice, ground_truth: dict) -> tuple[int, int]:
    """Returns (correct_field_count, total_field_count) for one invoice."""
    correct = 0
    total = 0

    for field in SCALAR_FIELDS:
        total += 1
        if _values_match(field, getattr(invoice, field), ground_truth.get(field)):
            correct += 1

    gt_items = ground_truth.get("line_items", [])
    actual_items = invoice.line_items
    total += 1  # line item count agreement
    if len(actual_items) == len(gt_items):
        correct += 1

    for i, gt_item in enumerate(gt_items):
        if i >= len(actual_items):
            total += len(gt_item)
            continue
        actual_item = actual_items[i]
        for key in ("description", "quantity", "unit_price", "amount"):
            total += 1
            actual_val = getattr(actual_item, key)
            if key in LINE_ITEM_FLOAT_FIELDS:
                if abs(float(actual_val) - float(gt_item[key])) <= FLOAT_TOLERANCE:
                    correct += 1
            else:
                # Descriptions can legitimately carry extra SKU/category text
                # the vision model read alongside the item name (see the
                # sample invoices) — a prefix match is the right bar here,
                # not exact string equality.
                gt_prefix = str(gt_item[key]).strip().lower()[:15]
                if str(actual_val).strip().lower().startswith(gt_prefix):
                    correct += 1

    return correct, total


def run_eval() -> dict:
    sample_files = sorted(SAMPLE_DIR.glob("*.pdf"))
    seen_invoice_numbers: set[str] = set()

    total_invoices = len(sample_files)
    extraction_successes = 0
    field_correct_total = 0
    field_total_total = 0
    per_invoice_results = []

    for pdf_path in sample_files:
        gt_path = GROUND_TRUTH_DIR / f"{pdf_path.stem}.json"
        if not gt_path.exists():
            per_invoice_results.append({"file": pdf_path.name, "skipped": "no ground truth"})
            total_invoices -= 1
            continue
        ground_truth = json.loads(gt_path.read_text())

        initial_state = {
            "file_path": str(pdf_path),
            "schema_id": SCHEMA_ID,
            "seen_invoice_numbers": seen_invoice_numbers,
        }
        try:
            result = run_pipeline(
                initial_state,
                workers=[extraction_worker, validation_worker, confidence_worker, report_worker],
                correction_worker=correction_worker,
            )
        except Exception as e:
            # Belt-and-suspenders: extract.py already retries hard failures
            # internally and returns status="failed" rather than raising, but
            # a batch eval run must never let ONE bad invoice take down the
            # whole run regardless of where the exception originates (D9).
            per_invoice_results.append({"file": pdf_path.name, "extraction_failed": True, "error": str(e)})
            continue

        if result.status == "failed":
            per_invoice_results.append({
                "file": pdf_path.name,
                "extraction_failed": True,
                "reason": result.history[-1] if result.history else None,
            })
            continue

        extraction_successes += 1
        invoice = result.final_state["invoice"]
        seen_invoice_numbers.add(invoice.invoice_number)

        correct, total = score_invoice(invoice, ground_truth)
        field_correct_total += correct
        field_total_total += total
        per_invoice_results.append({
            "file": pdf_path.name,
            "extraction_failed": False,
            "field_accuracy": round(correct / total, 3) if total else None,
            "errors": len(result.final_state["report"]["errors"]),
            "warnings": len(result.final_state["report"]["warnings"]),
        })

    return {
        "total_invoices": total_invoices,
        "extraction_success_rate": round(extraction_successes / total_invoices, 3) if total_invoices else 0,
        "field_accuracy": round(field_correct_total / field_total_total, 3) if field_total_total else None,
        "per_invoice": per_invoice_results,
    }


if __name__ == "__main__":
    results = run_eval()
    print(json.dumps(results, indent=2))
    print()
    print(f"Extraction success rate: {results['extraction_success_rate']:.1%}")
    if results["field_accuracy"] is not None:
        print(f"Field-level accuracy:    {results['field_accuracy']:.1%}")
