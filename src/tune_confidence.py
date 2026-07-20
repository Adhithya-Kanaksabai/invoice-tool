"""
tune_confidence.py — T-something (v2 Step 3.2): replaces the magic
CONFIDENCE_THRESHOLD=0.7 with a threshold chosen from evidence instead of a
guess.

This is only possible now that the test set has genuinely diverse documents
with real extraction errors (v2 Step 1) — on the original all-synthetic set
every field scored perfectly, so there was no (confidence, actually-wrong)
pair to learn a threshold FROM. Confidence tuning on a 100%-correct set is
meaningless; this is why Step 1 (diverse/real invoices) had to come before
this script could do anything.

Method: run the full pipeline over every document THAT HAS ground truth
(reusing eval.py's own dataset discovery and its _values_match — one
comparison logic, not two), and for every scalar field record
(confidence_score, was_this_field_actually_correct). Then sweep candidate
thresholds and report, for each: catch-rate (of the fields that were actually
WRONG, what fraction scored below the threshold and got flagged for a human)
and false-flag-rate (of the fields that were actually CORRECT, what fraction
got needlessly flagged anyway). The right threshold is a tradeoff, not a
single "best" number — this script's job is to make that tradeoff visible,
not to hide it behind one chosen constant.

Line-item fields are excluded — confidence.py only scores scalar fields
(get_scalar_field_names), so there's nothing to correlate for the list field.
No-ground-truth stress documents are naturally skipped (same as eval.py: no
GT file means nothing to compare against).
"""

from __future__ import annotations

import json

from confidence import confidence_worker
from eval import DATASETS, _values_match
from extract import extraction_worker
from orchestrator import run_pipeline
from report import report_worker
from retry import correction_worker
from schema_registry import get_scalar_field_names, get_schema
from validate import validation_worker

CANDIDATE_THRESHOLDS = [0.3, 0.35, 0.4, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 0.95]


def collect_field_observations() -> tuple[list[tuple[float, bool]], list[dict]]:
    """
    Returns (observations, wrong_details).

    observations: (confidence_score, was_actually_correct) per scalar field
    per document with ground truth — the input to threshold_table().

    wrong_details: full context (file, field, expected, actual, confidence,
    field_status) for every actually-wrong field, captured in
    THIS SAME run — not from a separate script invocation. The LLM is
    non-deterministic, so a second script run afterward would almost
    certainly diagnose a different set of "wrong" fields than the ones that
    produced this run's table — that mismatch is exactly the bug this
    function exists to avoid.
    """
    observations: list[tuple[float, bool]] = []
    wrong_details: list[dict] = []

    for dataset in DATASETS:
        schema_id = dataset["schema_id"]
        doc_schema = get_schema(schema_id)
        field_names = get_scalar_field_names(doc_schema)
        sample_dir = dataset["sample_dir"]
        ground_truth_dir = dataset["ground_truth_dir"]

        if not sample_dir.exists():
            continue
        sample_files = sorted(
            p
            for p in sample_dir.iterdir()
            if p.suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
        )

        seen_ids: set[str] = set()
        for sample_path in sample_files:
            gt_path = ground_truth_dir / f"{sample_path.stem}.json"
            if not gt_path.exists():
                continue  # no ground truth — same skip rule as eval.py
            ground_truth = json.loads(gt_path.read_text())

            result = run_pipeline(
                {
                    "file_path": str(sample_path),
                    "schema_id": schema_id,
                    "seen_document_ids": seen_ids,
                },
                workers=[
                    extraction_worker,
                    validation_worker,
                    confidence_worker,
                    report_worker,
                ],
                correction_worker=correction_worker,
            )
            if result.status == "failed":
                continue  # nothing to score — same as eval.py's extraction_failed skip

            document = result.final_state["document"]
            confidence = result.final_state["confidence"]

            for field_name in field_names:
                if field_name not in ground_truth:
                    continue  # this document's GT doesn't cover this field
                expected = ground_truth[field_name]
                actual = getattr(document, field_name, None)
                is_correct = _values_match(actual, expected)
                observations.append((confidence[field_name], is_correct))

                if not is_correct:
                    wrong_details.append(
                        {
                            "file": sample_path.name,
                            "field": field_name,
                            "expected": expected,
                            "actual": actual,
                            "confidence": confidence[field_name],
                            "field_status": str(document.field_status.get(field_name, "not set")),
                        }
                    )

    return observations, wrong_details


def threshold_table(observations: list[tuple[float, bool]]) -> list[dict]:
    wrong = [score for score, correct in observations if not correct]
    right = [score for score, correct in observations if correct]

    rows = []
    for threshold in CANDIDATE_THRESHOLDS:
        caught = sum(1 for s in wrong if s < threshold)
        false_flagged = sum(1 for s in right if s < threshold)
        rows.append(
            {
                "threshold": threshold,
                "catch_rate": round(caught / len(wrong), 3) if wrong else None,
                "false_flag_rate": round(false_flagged / len(right), 3) if right else None,
                "caught": caught,
                "total_wrong": len(wrong),
                "false_flagged": false_flagged,
                "total_right": len(right),
            }
        )
    return rows


def main() -> None:
    observations, wrong_details = collect_field_observations()
    total = len(observations)
    total_wrong = len(wrong_details)
    print(f"Collected {total} scalar-field observations ({total_wrong} actually wrong).\n")

    if wrong_details:
        print("Wrong-field detail (from THIS run, not a separate script invocation):")
        for detail in wrong_details:
            print(f"  {detail['file']} :: {detail['field']}")
            print(f"    expected={detail['expected']!r} actual={detail['actual']!r}")
            print(f"    confidence={detail['confidence']} field_status={detail['field_status']}")
        print()

    if total_wrong == 0:
        print(
            "No actually-wrong fields in the ground-truth set — cannot tune a threshold "
            "from zero errors. (This is expected on an all-synthetic set; it's exactly "
            "why Step 1's diverse/real documents had to exist first.)"
        )
        return

    rows = threshold_table(observations)
    print(f"{'threshold':>9} | {'catch rate':>10} | {'false-flag rate':>16} | caught/wrong | flagged/right")
    print("-" * 70)
    for row in rows:
        print(
            f"{row['threshold']:>9} | {row['catch_rate']:>10} | {row['false_flag_rate']:>16} | "
            f"{row['caught']}/{row['total_wrong']:<10} | {row['false_flagged']}/{row['total_right']}"
        )


if __name__ == "__main__":
    main()
