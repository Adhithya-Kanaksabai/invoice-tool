"""
eval_report.py — turns run_eval()'s result dict into a dated, human-readable
report card plus a machine-readable twin.

Named eval_report, not report: report.py is already the Report Worker (a
pipeline stage that summarises ONE document for the UI). This module summarises
ONE EVAL RUN across the whole dataset. Different altitude, different consumer —
worth keeping the names distinct rather than overloading "report".

Pure rendering, deliberately: it takes a dict and returns text. It makes no API
calls, reads no ground truth, and computes no accuracy of its own — every
number here is computed in eval.py and merely formatted here. That split is
what lets the whole renderer be unit-tested against a synthetic result dict
without spending a single token.

The .json twin exists so a future session can diff two runs mechanically
(regression comparison is explicitly out of scope for now) — the markdown is
for humans, the json is for the next tool.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

REPORTS_DIR = Path(__file__).parent.parent / "evals" / "reports"

# Stage names are worker function names (orchestrator keys latency by
# __name__). Prettify for display only — never for lookup.
_STAGE_LABELS = {
    "extraction_worker": "Extraction",
    "validation_worker": "Validation",
    "confidence_worker": "Confidence",
    "report_worker": "Report",
    "correction_worker": "Correction (agentic retry)",
}


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _num(value: float | None, places: int = 1) -> str:
    return "—" if value is None else f"{value:,.{places}f}"


def _usd(value: float | None) -> str:
    if value is None:
        return "—"
    # Sub-cent per-document costs are the norm here; four decimals keeps them
    # legible without pretending to more precision than the rate justifies.
    return f"${value:,.4f}"


def _stage_label(name: str) -> str:
    return _STAGE_LABELS.get(name, name.replace("_", " ").title())


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["_No data._", ""]
    return [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
        *["| " + " | ".join(row) + " |" for row in rows],
        "",
    ]


def build_report_markdown(results: dict, run_date: date | None = None) -> str:
    run_date = run_date or date.today()
    lines: list[str] = []

    lines += [
        f"# Extraction Eval Report — {run_date.isoformat()}",
        "",
        f"**Model:** `{results.get('model', 'unknown')}`  ",
        f"**Run at:** {results.get('generated_at', 'unknown')}  ",
        f"**Documents scored:** {results.get('overall_documents') or _total_documents(results)}",
        "",
        "## Headline",
        "",
    ]

    lines += _table(
        ["Metric", "Value"],
        [
            ["Field-level accuracy (all documents)", _pct(results.get("overall_field_accuracy"))],
            ["Extraction success rate", _pct(results.get("overall_extraction_success_rate"))],
            [
                "Avg tokens / document",
                _num(results.get("tokens", {}).get("avg_total_per_document"), 0),
            ],
            [
                "Avg estimated cost / document",
                _usd(results.get("cost", {}).get("estimated_avg_usd_per_document")),
            ],
            [
                "Avg end-to-end latency",
                _num((results.get("latency", {}).get("end_to_end_s") or {}).get("avg_s"), 2) + " s",
            ],
        ],
    )

    lines += [
        "## Accuracy by document category",
        "",
        "A single blended accuracy number hides which *kinds* of document the "
        "system actually fails on. Categories are defined in `tests/manifest.json`.",
        "",
    ]
    lines += _table(
        ["Category", "Docs", "Field accuracy", "Extraction success"],
        [
            [
                f"`{category}`",
                str(stats.get("documents", 0)),
                _pct(stats.get("field_accuracy")),
                _pct(stats.get("extraction_success_rate")),
            ]
            for category, stats in (results.get("by_category") or {}).items()
        ],
    )

    lines += ["## Accuracy by schema", ""]
    lines += _table(
        ["Schema", "Docs", "Field accuracy", "Extraction success"],
        [
            [
                f"`{schema_id}`",
                str(stats.get("total_documents", 0)),
                _pct(stats.get("field_accuracy")),
                _pct(stats.get("extraction_success_rate")),
            ]
            for schema_id, stats in (results.get("by_schema") or {}).items()
        ],
    )

    latency = results.get("latency") or {}
    lines += [
        "## Per-stage latency",
        "",
        "Wall-clock seconds per pipeline stage, measured in "
        "`orchestrator.run_pipeline`. A stage that ran twice for one document "
        "(re-validation after a correction round) reports its combined time.",
        "",
    ]
    # Three decimals, not two: the deterministic stages run in single-digit
    # milliseconds, and rounding them to "0.00" would hide the most useful
    # fact in this table — that essentially all the wall-clock time is the
    # one network call, and none of it is our own logic.
    def _stage_row(label: str, stats: dict) -> list[str]:
        return [
            label,
            str(stats.get("n", 0)),
            _num(stats.get("avg_s"), 3),
            _num(stats.get("p50_s"), 3),
            _num(stats.get("p95_s"), 3),
            _num(stats.get("max_s"), 3),
        ]

    stage_rows = [
        _stage_row(_stage_label(stage), stats)
        for stage, stats in (latency.get("per_stage_s") or {}).items()
        if stats
    ]
    end_to_end = latency.get("end_to_end_s")
    if end_to_end:
        stage_rows.append(_stage_row("**End-to-end**", end_to_end))
    lines += _table(["Stage", "n", "avg (s)", "p50 (s)", "p95 (s)", "max (s)"], stage_rows)

    tokens = results.get("tokens") or {}
    cost = results.get("cost") or {}
    lines += [
        "## Tokens and cost",
        "",
        "Token counts are **measured** — read from each Gemini response's "
        "`usage_metadata`, summed across extraction retries and any agentic "
        "correction turns. Cost is **derived** from a published rate and is an "
        "estimate only.",
        "",
    ]
    totals = tokens.get("total") or {}
    lines += _table(
        ["Metric", "Value"],
        [
            ["Documents measured", str(tokens.get("documents_measured", 0))],
            ["Total API calls", _num(totals.get("calls"), 0)],
            ["Avg API calls / document", _num(tokens.get("avg_calls_per_document"), 2)],
            ["Total input tokens", _num(totals.get("prompt"), 0)],
            ["Total output tokens", _num(totals.get("candidates"), 0)],
            ["Avg input tokens / document", _num(tokens.get("avg_prompt_per_document"), 0)],
            ["Avg output tokens / document", _num(tokens.get("avg_candidates_per_document"), 0)],
            ["Estimated total cost", _usd(cost.get("estimated_total_usd"))],
            ["Estimated cost / document", _usd(cost.get("estimated_avg_usd_per_document"))],
        ],
    )
    lines += [
        f"> **Cost is an estimate**, {cost.get('rate_label', 'no rate configured')}. "
        "Published rates change; the token counts above do not.",
        "",
    ]

    failures = results.get("failures") or {}
    lines += ["## Failures", ""]
    if not failures.get("count"):
        lines += ["No extraction failures in this run.", ""]
    else:
        lines += [
            f"**{failures['count']}** document(s) failed extraction outright "
            "(excluded from field accuracy, counted against extraction success rate).",
            "",
            "Most common reason:",
            "",
            f"> {failures.get('most_common_reason')}",
            "",
        ]
        lines += _table(
            ["Count", "Reason"],
            [
                [str(count), reason]
                for reason, count in (failures.get("reason_counts") or {}).items()
            ],
        )

    lines += [
        "---",
        "",
        "_Generated by `src/eval_report.py` from a live run of `src/eval.py` "
        "(cache disabled — every document was re-extracted against the real API)._",
    ]
    return "\n".join(lines) + "\n"


def _total_documents(results: dict) -> int:
    return sum(
        stats.get("total_documents", 0) for stats in (results.get("by_schema") or {}).values()
    )


def write_report(
    results: dict, out_dir: Path | None = None, run_date: date | None = None
) -> dict[str, Path]:
    """
    Write `eval_<YYYY-MM-DD>.md` and `eval_<YYYY-MM-DD>.json` side by side.
    Returns both paths. Re-running on the same day overwrites that day's
    report rather than piling up near-duplicates.
    """
    run_date = run_date or date.today()
    out_dir = out_dir or REPORTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = f"eval_{run_date.isoformat()}"
    md_path = out_dir / f"{stem}.md"
    json_path = out_dir / f"{stem}.json"

    md_path.write_text(build_report_markdown(results, run_date=run_date), encoding="utf-8")
    json_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    return {"markdown": md_path, "json": json_path}
