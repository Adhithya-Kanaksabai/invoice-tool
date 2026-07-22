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
from collections import Counter
from datetime import datetime
from pathlib import Path

from confidence import confidence_worker
from eval_report import write_report
from extract import MODEL_NAME, extraction_worker
from ingest import compute_content_hash
from llm_usage import TOKENS_KEY, empty_usage, estimate_cost_usd, pricing_label
from orchestrator import LATENCY_KEY, run_pipeline
from persistence import check_natural_id_exists, persist_pipeline_result
from report import report_worker
from retry import correction_worker
from schema_registry import get_list_field_name, get_schema
from validate import validation_worker

TESTS_DIR = Path(__file__).parent.parent / "tests"
FLOAT_TOLERANCE = 0.01
MANIFEST_PATH = TESTS_DIR / "manifest.json"
UNCATEGORIZED = "uncategorized"

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


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, str]:
    """
    filename -> category, from tests/manifest.json.

    A missing manifest is not an error: the eval still runs and everything
    lands in one `uncategorized` bucket. Stratification is a reporting
    improvement, not a precondition for measuring accuracy.
    """
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("files", {})


def _percentile(values: list[float], pct: float) -> float | None:
    """
    Nearest-rank percentile. Deliberately not statistics.quantiles(), which
    needs at least two data points and interpolates — with an n of 29, an
    interpolated p95 would be a fiction dressed up as a measurement. Nearest-
    rank always returns a value that was actually observed.
    """
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(pct / 100 * len(ordered) + 0.5)) - 1))
    return ordered[index]


def _summarize_latency(samples: list[float]) -> dict | None:
    if not samples:
        return None
    return {
        "n": len(samples),
        "avg_s": round(sum(samples) / len(samples), 3),
        "p50_s": round(_percentile(samples, 50), 3),
        "p95_s": round(_percentile(samples, 95), 3),
        "max_s": round(max(samples), 3),
    }


def _accuracy(correct: int, total: int) -> float | None:
    return round(correct / total, 3) if total else None


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:.1%}"


def run_eval() -> dict:
    overall = {"total_invoices": 0, "extraction_successes": 0, "field_correct": 0, "field_total": 0}
    per_dataset: dict[str, dict] = {}

    manifest = load_manifest()
    # Stratified accumulators (D-new): the same three counters as `overall`,
    # but bucketed by document category, so a strong score on clean synthetic
    # PDFs can't quietly average away a weak one on degraded scans.
    by_category: dict[str, dict] = {}
    # Observability accumulators. Kept as raw per-document samples rather than
    # running means so percentiles are computable — an average latency hides
    # exactly the tail the retry loop creates.
    stage_latency_samples: dict[str, list[float]] = {}
    total_latency_samples: list[float] = []
    token_samples: list[dict] = []
    failure_reasons: list[str] = []

    def _bucket(category: str) -> dict:
        return by_category.setdefault(
            category,
            {"documents": 0, "extraction_successes": 0, "field_correct": 0, "field_total": 0},
        )

    def _record_observability(final_state: dict) -> tuple[dict, dict, float]:
        """Pull the two reserved metadata keys off a finished pipeline state."""
        tokens = dict(final_state.get(TOKENS_KEY) or empty_usage())
        stages = dict(final_state.get(LATENCY_KEY) or {})
        for stage_name, seconds in stages.items():
            stage_latency_samples.setdefault(stage_name, []).append(seconds)
        total_seconds = sum(stages.values())
        total_latency_samples.append(total_seconds)
        if tokens.get("calls"):
            token_samples.append(tokens)
        return tokens, stages, total_seconds

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
            category = manifest.get(sample_path.name, UNCATEGORIZED)
            bucket = _bucket(category)
            bucket["documents"] += 1

            initial_state = {
                "file_path": str(sample_path),
                "schema_id": schema_id,
                "seen_document_ids": seen_ids,
                "duplicate_checker": check_natural_id_exists,
                # eval.py's whole point is measuring LIVE extraction accuracy
                # against ground truth on every run — the same sample files
                # get re-extracted every time this is run. Without this flag,
                # the content-hash cache (extract.py) would silently replay
                # a stale result from a prior eval run instead of actually
                # calling Gemini, freezing the eval numbers. See extract.py.
                "skip_cache": True,
            }
            started_at = datetime.utcnow()
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
                failure_reasons.append(f"pipeline raised: {type(e).__name__}")
                per_invoice_results.append(
                    {
                        "file": sample_path.name,
                        "category": category,
                        "extraction_failed": True,
                        "error": str(e),
                    }
                )
                continue

            # Persist regardless of outcome (a failed extraction is still a
            # real, queryable fact) — but a persistence hiccup on ONE
            # document must not take down the whole eval run either, same
            # D9 isolation as the exception guard above. Visibly recorded in
            # the results either way, never silently swallowed.
            try:
                persist_pipeline_result(
                    result,
                    original_filename=sample_path.name,
                    content_hash=compute_content_hash(sample_path),
                    started_at=started_at,
                )
            except Exception as e:
                per_invoice_results.append({"file": sample_path.name, "persistence_failed": str(e)})

            tokens, stages, total_seconds = _record_observability(result.final_state)
            estimated_cost = estimate_cost_usd(
                tokens.get("prompt", 0), tokens.get("candidates", 0), MODEL_NAME
            )
            observability = {
                "category": category,
                "tokens": tokens,
                "stage_latency_s": {k: round(v, 3) for k, v in stages.items()},
                "total_latency_s": round(total_seconds, 3),
                "estimated_cost_usd": (
                    round(estimated_cost, 6) if estimated_cost is not None else None
                ),
            }

            if result.status == "failed":
                failure_reasons.append(result.reason or "unspecified failure")
                per_invoice_results.append(
                    {
                        "file": sample_path.name,
                        **observability,
                        "extraction_failed": True,
                        "reason": result.reason,
                    }
                )
                continue

            dataset_successes += 1
            bucket["extraction_successes"] += 1
            document = result.final_state["document"]
            doc_id = getattr(document, dataset["id_field"], None)
            if doc_id:
                seen_ids.add(doc_id)

            correct, total = score_document(document, ground_truth, list_field)
            dataset_field_correct += correct
            dataset_field_total += total
            bucket["field_correct"] += correct
            bucket["field_total"] += total
            per_invoice_results.append(
                {
                    "file": sample_path.name,
                    **observability,
                    "extraction_failed": False,
                    "field_accuracy": _accuracy(correct, total),
                    "errors": len(result.final_state["report"]["errors"]),
                    "warnings": len(result.final_state["report"]["warnings"]),
                }
            )

        per_dataset[schema_id] = {
            "total_documents": dataset_total,
            "extraction_success_rate": _accuracy(dataset_successes, dataset_total) or 0,
            "field_accuracy": _accuracy(dataset_field_correct, dataset_field_total),
            "per_document": per_invoice_results,
        }

        overall["total_invoices"] += dataset_total
        overall["extraction_successes"] += dataset_successes
        overall["field_correct"] += dataset_field_correct
        overall["field_total"] += dataset_field_total

    documents_measured = len(token_samples)
    total_tokens = {
        key: sum(sample.get(key, 0) for sample in token_samples)
        for key in ("prompt", "candidates", "total", "calls")
    }
    total_cost = estimate_cost_usd(total_tokens["prompt"], total_tokens["candidates"], MODEL_NAME)

    def _avg(value: float) -> float | None:
        return round(value / documents_measured, 3) if documents_measured else None

    return {
        "model": MODEL_NAME,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "overall_extraction_success_rate": (
            _accuracy(overall["extraction_successes"], overall["total_invoices"]) or 0
        ),
        "overall_field_accuracy": _accuracy(overall["field_correct"], overall["field_total"]),
        "by_schema": per_dataset,
        "by_category": {
            category: {
                "documents": stats["documents"],
                "extraction_success_rate": _accuracy(
                    stats["extraction_successes"], stats["documents"]
                ),
                "field_accuracy": _accuracy(stats["field_correct"], stats["field_total"]),
            }
            for category, stats in sorted(by_category.items())
        },
        "latency": {
            "per_stage_s": {
                stage: _summarize_latency(samples)
                for stage, samples in sorted(stage_latency_samples.items())
            },
            "end_to_end_s": _summarize_latency(total_latency_samples),
        },
        "tokens": {
            "documents_measured": documents_measured,
            "total": total_tokens,
            "avg_prompt_per_document": _avg(total_tokens["prompt"]),
            "avg_candidates_per_document": _avg(total_tokens["candidates"]),
            "avg_total_per_document": _avg(total_tokens["total"]),
            "avg_calls_per_document": _avg(total_tokens["calls"]),
        },
        "cost": {
            # Derived, not measured — see llm_usage.py. The label travels
            # with the number everywhere it's rendered.
            "estimated_total_usd": round(total_cost, 6) if total_cost is not None else None,
            "estimated_avg_usd_per_document": (
                round(total_cost / documents_measured, 6)
                if total_cost is not None and documents_measured
                else None
            ),
            "rate_label": pricing_label(MODEL_NAME),
        },
        "failures": {
            "count": len(failure_reasons),
            "most_common_reason": (
                Counter(failure_reasons).most_common(1)[0][0] if failure_reasons else None
            ),
            "reason_counts": dict(Counter(failure_reasons).most_common()),
        },
    }


if __name__ == "__main__":
    results = run_eval()
    print(json.dumps(results, indent=2))
    print()
    for category, stats in results["by_category"].items():
        print(
            f"[{category:>18}] n={stats['documents']:>2}  "
            f"field accuracy: {_pct(stats['field_accuracy'])}  "
            f"extraction success: {_pct(stats['extraction_success_rate'])}"
        )
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

    paths = write_report(results)
    print()
    print(f"Report written to {paths['markdown']}")
    print(f"            and   {paths['json']}")
