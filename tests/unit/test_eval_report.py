"""
Unit tests for eval_report.py — pure rendering, fed a synthetic result dict.
No API calls, no ground truth, no tokens spent: that separation is the whole
reason the renderer takes a dict instead of running the eval itself.
"""

import json

from eval_report import build_report_markdown, write_report


def _fake_results() -> dict:
    return {
        "model": "gemini-3.1-flash-lite",
        "generated_at": "2026-07-22T10:00:00Z",
        "overall_extraction_success_rate": 0.966,
        "overall_field_accuracy": 0.912,
        "by_schema": {
            "invoice-v1": {
                "total_documents": 24,
                "extraction_success_rate": 0.958,
                "field_accuracy": 0.905,
                "per_document": [],
            },
            "receipt-v1": {
                "total_documents": 5,
                "extraction_success_rate": 1.0,
                "field_accuracy": 0.95,
                "per_document": [],
            },
        },
        "by_category": {
            "clean_synthetic": {
                "documents": 8,
                "extraction_success_rate": 1.0,
                "field_accuracy": 0.99,
            },
            "degraded_synthetic": {
                "documents": 10,
                "extraction_success_rate": 0.9,
                "field_accuracy": 0.81,
            },
        },
        "latency": {
            "per_stage_s": {
                "extraction_worker": {
                    "n": 29,
                    "avg_s": 4.2,
                    "p50_s": 3.9,
                    "p95_s": 7.1,
                    "max_s": 8.0,
                },
                "validation_worker": {
                    "n": 29,
                    "avg_s": 0.01,
                    "p50_s": 0.01,
                    "p95_s": 0.02,
                    "max_s": 0.02,
                },
            },
            "end_to_end_s": {"n": 29, "avg_s": 4.5, "p50_s": 4.0, "p95_s": 7.4, "max_s": 8.3},
        },
        "tokens": {
            "documents_measured": 29,
            "total": {"prompt": 290000, "candidates": 29000, "total": 319000, "calls": 31},
            "avg_prompt_per_document": 10000.0,
            "avg_candidates_per_document": 1000.0,
            "avg_total_per_document": 11000.0,
            "avg_calls_per_document": 1.07,
        },
        "cost": {
            "estimated_total_usd": 0.1160,
            "estimated_avg_usd_per_document": 0.004,
            "rate_label": "estimated at $0.25/1M input tokens and $1.50/1M output tokens "
            "(rate as of 2026-07-22)",
        },
        "failures": {
            "count": 1,
            "most_common_reason": "Could not extract a valid Invoice from this document",
            "reason_counts": {"Could not extract a valid Invoice from this document": 1},
        },
    }


def test_markdown_reports_model_and_headline_numbers():
    md = build_report_markdown(_fake_results())
    assert "gemini-3.1-flash-lite" in md
    assert "91.2%" in md  # overall field accuracy
    assert "96.6%" in md  # extraction success rate


def test_markdown_breaks_accuracy_out_by_category():
    md = build_report_markdown(_fake_results())
    assert "clean_synthetic" in md
    assert "degraded_synthetic" in md
    assert "99.0%" in md
    assert "81.0%" in md


def test_markdown_shows_per_stage_latency_percentiles():
    md = build_report_markdown(_fake_results())
    assert "Extraction" in md
    assert "7.100" in md  # p95 for extraction
    assert "End-to-end" in md


def test_markdown_always_labels_cost_as_an_estimate():
    """
    The one rule this report must never break: tokens are measured, cost is
    derived. A cost figure without its rate disclaimer is a fabricated fact.
    """
    md = build_report_markdown(_fake_results())
    assert "estimate" in md.lower()
    assert "rate as of 2026-07-22" in md
    assert "$0.0040" in md  # avg cost per document


def test_markdown_surfaces_the_most_common_failure_reason():
    md = build_report_markdown(_fake_results())
    assert "Could not extract a valid Invoice" in md


def test_markdown_handles_a_clean_run_with_no_failures():
    results = _fake_results()
    results["failures"] = {"count": 0, "most_common_reason": None, "reason_counts": {}}
    md = build_report_markdown(results)
    assert "No extraction failures" in md


def test_markdown_renders_missing_sections_without_crashing():
    """
    A partial result dict (e.g. an eval that failed halfway) must still
    render — a report generator that raises tells you nothing at all.
    """
    md = build_report_markdown({"model": "x"})
    assert "Extraction Eval Report" in md
    assert "—" in md  # em-dash placeholder for absent numbers


def test_write_report_writes_dated_markdown_and_json_pair(tmp_path):
    from datetime import date

    results = _fake_results()
    paths = write_report(results, out_dir=tmp_path, run_date=date(2026, 7, 22))

    assert paths["markdown"].name == "eval_2026-07-22.md"
    assert paths["json"].name == "eval_2026-07-22.json"
    assert "gemini-3.1-flash-lite" in paths["markdown"].read_text(encoding="utf-8")
    # the json twin must be the full result dict, losslessly, for a future
    # run-over-run comparison to have something mechanical to diff
    assert json.loads(paths["json"].read_text(encoding="utf-8")) == results


def test_write_report_overwrites_same_day_rather_than_piling_up(tmp_path):
    from datetime import date

    write_report(_fake_results(), out_dir=tmp_path, run_date=date(2026, 7, 22))
    write_report(_fake_results(), out_dir=tmp_path, run_date=date(2026, 7, 22))
    assert len(list(tmp_path.glob("eval_*.md"))) == 1
