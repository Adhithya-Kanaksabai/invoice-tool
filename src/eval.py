"""
eval.py — T13/D17. Runs the full pipeline over the test set(s) and reports
field accuracy + extraction success rate, per tasks.md's own note: "the
strongest resume signal in the whole project. Do not skip this."

Generalized (D17) to run over multiple registered schemas — invoice-v1 and
receipt-v1 — each with its own sample/ground-truth directory, deriving which
fields to compare from each ground-truth file's own keys rather than a
hardcoded invoice-specific field list. A field is compared numerically (with
tolerance) if its ground-truth value is a number, string-compared otherwise —
this needs no per-schema field-type list at all.

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
from schema_registry import get_list_field_name, get_schema
from validate import validation_worker

TESTS_DIR = Path(__file__).parent.parent / "tests"
FLOAT_TOLERANCE = 0.01

# Each dataset: schema_id, its sample dir, its ground-truth dir, and the
# field this schema uses for "duplicate within batch" tracking (the ONE bit
# of per-dataset config eval.py needs, since schema_registry doesn't encode
# "which field is the natural dedup id" — everything else is derived.
DATASETS = [
    {
        "schema_id": "invoice-v1",
        "sample_dir": TESTS_DIR / "sample_invoices",
        "ground_truth_dir": TESTS_DIR / "ground_truth",
        "id_field": "invoice_number",
    },
    {
        "schema_id": "receipt-v1",
        "sample_dir": TESTS_DIR / "sample_receipts",
        "ground_truth_dir": TESTS_DIR / "ground_truth_receipts",
        "id_field": "transaction_id",
    },
]


def _values_match(actual, expected) -> bool:
    if expected is None and actual is None:
        return True
    if expected is None or actual is None:
        return False
    if isinstance(expected, int | float) and not isinstance(expected, bool):
        return abs(float(actual) - float(expected)) <= FLOAT_TOLERANCE
    return str(actual).strip().lower() == str(expected).strip().lower()


def score_document(document, ground_truth: dict, list_field: str) -> tuple[int, int]:
    """Returns (correct_field_count, total_field_count) for one document."""
    correct = 0
    total = 0

    for key, expected in ground_truth.items():
        if key == list_field:
            continue
        total += 1
        if _values_match(getattr(document, key, None), expected):
            correct += 1

    gt_items = ground_truth.get(list_field, [])
    actual_items = getattr(document, list_field)
    total += 1  # item count agreement
    if len(actual_items) == len(gt_items):
        correct += 1

    for i, gt_item in enumerate(gt_items):
        if i >= len(actual_items):
            total += len(gt_item)
            continue
        actual_item = actual_items[i]
        for key, expected in gt_item.items():
            total += 1
            actual_val = getattr(actual_item, key)
            if key == "description":
                # Descriptions can legitimately carry extra SKU/category
                # text the vision model read alongside the item name (see
                # the original SuperStore-template samples) — a prefix
                # match is the right bar, not exact string equality.
                if str(actual_val).strip().lower().startswith(str(expected).strip().lower()[:15]):
                    correct += 1
            elif _values_match(actual_val, expected):
                correct += 1

    return correct, total


def run_eval() -> dict:
    overall = {"total_invoices": 0, "extraction_successes": 0, "field_correct": 0, "field_total": 0}
    per_dataset: dict[str, dict] = {}

    for dataset in DATASETS:
        schema_id = dataset["schema_id"]
        doc_schema = get_schema(schema_id)
        list_field = get_list_field_name(doc_schema)
        sample_dir: Path = dataset["sample_dir"]
        ground_truth_dir: Path = dataset["ground_truth_dir"]

        sample_files = (
            sorted(
                p
                for p in sample_dir.iterdir()
                if p.suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
            )
            if sample_dir.exists()
            else []
        )
        seen_ids: set[str] = set()

        dataset_total = len(sample_files)
        dataset_successes = 0
        dataset_field_correct = 0
        dataset_field_total = 0
        per_invoice_results = []

        for sample_path in sample_files:
            gt_path = ground_truth_dir / f"{sample_path.stem}.json"
            if not gt_path.exists():
                per_invoice_results.append({"file": sample_path.name, "skipped": "no ground truth"})
                dataset_total -= 1
                continue
            ground_truth = json.loads(gt_path.read_text())

            initial_state = {
                "file_path": str(sample_path),
                "schema_id": schema_id,
                "seen_document_ids": seen_ids,
            }
            try:
                result = run_pipeline(
                    initial_state,
                    workers=[
                        extraction_worker,
                        validation_worker,
                        confidence_worker,
                        report_worker,
                    ],
                    correction_worker=correction_worker,
                )
            except Exception as e:
                # Belt-and-suspenders: extract.py already retries hard
                # failures internally and returns status="failed" rather
                # than raising, but a batch eval run must never let ONE bad
                # document take down the whole run regardless of where the
                # exception originates (D9).
                per_invoice_results.append(
                    {"file": sample_path.name, "extraction_failed": True, "error": str(e)}
                )
                continue

            if result.status == "failed":
                per_invoice_results.append(
                    {"file": sample_path.name, "extraction_failed": True, "reason": result.reason}
                )
                continue

            dataset_successes += 1
            document = result.final_state["document"]
            doc_id = getattr(document, dataset["id_field"], None)
            if doc_id:
                seen_ids.add(doc_id)

            correct, total = score_document(document, ground_truth, list_field)
            dataset_field_correct += correct
            dataset_field_total += total
            per_invoice_results.append(
                {
                    "file": sample_path.name,
                    "extraction_failed": False,
                    "field_accuracy": round(correct / total, 3) if total else None,
                    "errors": len(result.final_state["report"]["errors"]),
                    "warnings": len(result.final_state["report"]["warnings"]),
                }
            )

        per_dataset[schema_id] = {
            "total_documents": dataset_total,
            "extraction_success_rate": round(dataset_successes / dataset_total, 3)
            if dataset_total
            else 0,
            "field_accuracy": round(dataset_field_correct / dataset_field_total, 3)
            if dataset_field_total
            else None,
            "per_document": per_invoice_results,
        }

        overall["total_invoices"] += dataset_total
        overall["extraction_successes"] += dataset_successes
        overall["field_correct"] += dataset_field_correct
        overall["field_total"] += dataset_field_total

    return {
        "overall_extraction_success_rate": (
            round(overall["extraction_successes"] / overall["total_invoices"], 3)
            if overall["total_invoices"]
            else 0
        ),
        "overall_field_accuracy": (
            round(overall["field_correct"] / overall["field_total"], 3)
            if overall["field_total"]
            else None
        ),
        "by_schema": per_dataset,
    }


if __name__ == "__main__":
    results = run_eval()
    print(json.dumps(results, indent=2))
    print()
    for schema_id, stats in results["by_schema"].items():
        print(f"[{schema_id}] extraction success: {stats['extraction_success_rate']:.1%}", end="  ")
        if stats["field_accuracy"] is not None:
            print(f"field accuracy: {stats['field_accuracy']:.1%}")
        else:
            print()
    print()
    print(f"Overall extraction success rate: {results['overall_extraction_success_rate']:.1%}")
    if results["overall_field_accuracy"] is not None:
        print(f"Overall field-level accuracy:    {results['overall_field_accuracy']:.1%}")
