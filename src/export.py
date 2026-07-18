"""
export.py — plain I/O, not a Worker (design.md: "nothing to orchestrate").

Writes the final pipeline state as JSON and CSV. Both formats preserve the
PASS/Warnings/Errors grouping from report.py, per FR9.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


def export_json(state: dict, out_path: str | Path) -> None:
    invoice = state.get("invoice")
    payload = {
        "invoice": invoice.model_dump(mode="json") if invoice else None,
        "report": state.get("report", {}),
        "extraction_failed": state.get("extraction_failed", False),
    }
    Path(out_path).write_text(json.dumps(payload, indent=2))


def export_csv(state: dict, out_path: str | Path) -> None:
    report = state.get("report", {})
    rows = []
    for group_name, entries in report.items():
        for entry in entries:
            flag_reasons = "; ".join(f["reason"] for f in entry.get("flags", []))
            rows.append({
                "group": group_name,
                "field": entry["field"],
                "value": entry["value"],
                "field_status": entry["field_status"],
                "confidence": entry["confidence"],
                "source_note": entry["source_note"],
                "flag_reasons": flag_reasons,
            })

    if not rows:
        Path(out_path).write_text("")
        return

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
