"""
cord_eval.py — runs the pipeline against the external CORD-v2 benchmark
subset (see tests/fetch_cord_benchmark.py) and reports field accuracy against
it, separately from eval.py's hand-verified 29-document run.

Why a separate module rather than folding this into eval.py's DATASETS list:
CORD's ground truth only covers `total` and item descriptions (see
fetch_cord_benchmark.py's docstring for why merchant/date/tax/subtotal aren't
included) -- mixing a narrower-scored, externally-sourced dataset into the
same DATASETS loop as the hand-verified set would make one blended number out
of two differently-shaped measurements. Reuses eval.py's own scoring
functions (score_document, _values_match) rather than reimplementing them, so
"correct" means the same thing in both places.

This is an out-of-distribution check, not a replacement for the primary eval:
CORD is real-world Indonesian retail receipts, a different distribution than
this project's own 29-document set, and its own ground truth here is
narrower. A high score says the extraction generalizes past the documents it
was tuned against; a low score says the opposite. Either way it's a more
honest number than only ever measuring against documents chosen in-house.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from confidence import confidence_worker
from eval import score_document
from extract import extraction_worker
from orchestrator import run_pipeline
from report import report_worker
from retry import correction_worker
from schema_registry import get_list_field_name, get_schema
from validate import validation_worker

BENCHMARK_DIR = Path(__file__).parent.parent / "tests" / "cord_benchmark"
IMAGES_DIR = BENCHMARK_DIR / "images"
GROUND_TRUTH_DIR = BENCHMARK_DIR / "ground_truth"
REPORTS_DIR = Path(__file__).parent.parent / "evals" / "benchmarks"

SCHEMA_ID = "receipt-v1"


def run_cord_eval() -> dict:
    doc_schema = get_schema(SCHEMA_ID)
    list_field = get_list_field_name(doc_schema)

    sample_files = sorted(IMAGES_DIR.glob("*.jpg")) if IMAGES_DIR.exists() else []

    total_documents = len(sample_files)
    extraction_successes = 0
    field_correct = 0
    field_total = 0
    per_document = []

    for sample_path in sample_files:
        gt_path = GROUND_TRUTH_DIR / f"{sample_path.stem}.json"
        if not gt_path.exists():
            per_document.append({"file": sample_path.name, "skipped": "no ground truth"})
            total_documents -= 1
            continue
        ground_truth = json.loads(gt_path.read_text())

        try:
            result = run_pipeline(
                {
                    "file_path": str(sample_path),
                    "schema_id": SCHEMA_ID,
                    "skip_cache": True,
                },
                workers=[
                    extraction_worker,
                    validation_worker,
                    confidence_worker,
                    report_worker,
                ],
                correction_worker=correction_worker,
            )
        except Exception as e:
            per_document.append(
                {"file": sample_path.name, "extraction_failed": True, "error": str(e)}
            )
            continue

        if result.status == "failed":
            per_document.append(
                {"file": sample_path.name, "extraction_failed": True, "reason": result.reason}
            )
            continue

        extraction_successes += 1
        document = result.final_state["document"]
        correct, total = score_document(document, ground_truth, list_field)
        field_correct += correct
        field_total += total
        per_document.append(
            {
                "file": sample_path.name,
                "extraction_failed": False,
                "field_accuracy": round(correct / total, 3) if total else None,
            }
        )

    return {
        "benchmark": "CORD-v2 (naver-clova-ix/cord-v2, external, CC-BY-4.0)",
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "total_documents": total_documents,
        "extraction_success_rate": (
            round(extraction_successes / total_documents, 3) if total_documents else 0
        ),
        "field_accuracy": round(field_correct / field_total, 3) if field_total else None,
        "note": (
            "Ground truth here only covers `total` and item descriptions -- narrower "
            "than the hand-verified 29-document set's full field coverage. See "
            "tests/fetch_cord_benchmark.py for why."
        ),
        "per_document": per_document,
    }


def write_cord_report(results: dict, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.utcnow().date().isoformat()
    path = out_dir / f"cord_v2_{date_str}.json"
    path.write_text(json.dumps(results, indent=2))
    return path


if __name__ == "__main__":
    results = run_cord_eval()
    print(json.dumps(results, indent=2))
    print()
    print(f"CORD-v2 benchmark: {results['total_documents']} documents")
    print(f"Extraction success rate: {results['extraction_success_rate']:.1%}")
    if results["field_accuracy"] is not None:
        print(f"Field-level accuracy (total + item descriptions): {results['field_accuracy']:.1%}")
    path = write_cord_report(results)
    print(f"\nReport written to {path}")
