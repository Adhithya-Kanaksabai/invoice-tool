"""
app.py — Streamlit UI.

Per D8: Streamlit, not React — this project demonstrates AI systems
engineering, not frontend engineering. Per D10: the source image is always
shown next to the output (st.columns([1, 1])) — without this, a human can't
actually verify an extraction. Per D14, each field's source_note is shown
next to its value — the citation-level grounding this project ships instead
of pixel-level bounding boxes.

Schema-driven, not hardcoded to invoices (per D17): the document-type
selector passes schema_id straight into run_pipeline, same generic path
extract.py/validate.py/confidence.py/report.py/retry.py already use.

The "Pipeline stages" and "Agentic Correction" sections exist specifically to
answer a real gap found during review: state carries retried_fields /
correction_note / correction_used_fallback, but nothing displayed them —
so the one agentic component in this project was invisible from the UI.
Both are now driven directly off orchestrator.py's own PipelineResult.history
and final_state, not re-derived guesses.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from confidence import confidence_worker
from export import export_csv, export_json
from extract import extraction_worker
from orchestrator import run_pipeline
from report import report_worker
from retry import correction_worker
from schema_registry import get_list_field_name, get_schema
from validate import validation_worker

st.set_page_config(page_title="Invoice Intelligence Tool", page_icon="🧾", layout="wide")

DOCUMENT_TYPES = {
    "Invoice": "invoice-v1",
    "Receipt": "receipt-v1",
}

STAGE_LABELS = {
    "extraction_worker": "Extract (vision LLM)",
    "validation_worker": "Validate (schema + business rules)",
    "correction_worker": "🤖 Agentic correction",
    "confidence_worker": "Score confidence",
    "report_worker": "Build report",
}

SAMPLE_DIR = Path(__file__).parent.parent / "tests" / "sample_invoices"


st.title("🧾 Invoice Intelligence Tool")
st.caption(
    "Vision-LLM extraction, two-layer validation, heuristic confidence, and one bounded "
    "agentic Correction Worker — built on a generic orchestrator/worker pipeline."
)

badge_row = st.container()
with badge_row:
    b1, b2, b3, b4, _ = st.columns([1, 1, 1, 1, 2])
    b1.badge("Orchestrator + Workers", color="blue")
    b2.badge("Gemini Vision", color="violet")
    b3.badge("Pydantic validation", color="green")
    b4.badge("1 agentic loop", color="orange")

st.divider()

col_controls, col_upload = st.columns([1, 2])
with col_controls:
    doc_type_label = st.selectbox("Document type", list(DOCUMENT_TYPES.keys()))
    schema_id = DOCUMENT_TYPES[doc_type_label]

    sample_choices = ["(none — upload below)"]
    if schema_id == "invoice-v1":
        sample_choices += sorted(p.name for p in SAMPLE_DIR.glob("*.pdf"))
    sample_pick = st.selectbox("Or try a sample invoice", sample_choices)

with col_upload:
    uploaded = st.file_uploader(
        f"Upload a {doc_type_label.lower()}", type=["pdf", "jpg", "jpeg", "png", "webp"]
    )

file_path: str | None = None
if uploaded:
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as tmp:
        tmp.write(uploaded.getvalue())
        file_path = tmp.name
elif sample_pick != "(none — upload below)":
    file_path = str(SAMPLE_DIR / sample_pick)


def _render_pipeline_stages(history: list[str]) -> None:
    """
    Only called once the pipeline has already completed successfully, so
    every stage except correction_worker is guaranteed to be in `history` —
    correction_worker is the one stage that's genuinely conditional (it only
    runs if the orchestrator's retry branch fired), which is exactly what
    this is meant to make visible.
    """
    st.subheader("Pipeline stages")
    ran = set(history)
    all_stages = [
        "extraction_worker",
        "validation_worker",
        "correction_worker",
        "confidence_worker",
        "report_worker",
    ]
    cols = st.columns(len(all_stages))
    for col, stage in zip(cols, all_stages):
        label = STAGE_LABELS[stage]
        if stage in ran:
            col.badge(label, icon="✅", color="green")
        else:
            col.badge(label, icon="⏭️", color="gray")  # correction_worker: not needed this run


def _render_agentic_panel(final_state: dict) -> None:
    st.subheader("Agentic Correction Worker")
    retried_fields = final_state.get("retried_fields")
    if not retried_fields:
        st.badge("Not needed — passed validation on the first pass", icon="✅", color="green")
        return

    used_fallback = final_state.get("correction_used_fallback")
    with st.container(border=True):
        st.badge("Correction fired", icon="🤖", color="orange")
        st.markdown(f"**Fields re-examined:** {', '.join(sorted(retried_fields))}")
        note = final_state.get("correction_note")
        if note:
            st.markdown(f"**Model's rationale:** _{note}_")
        if used_fallback:
            st.caption(
                "⚠️ Used the deterministic single-shot fallback (tool-calling didn't converge "
                "within the turn limit) — see design.md D6."
            )
        else:
            st.caption("Resolved via real tool-calling (reexamine → submit_correction).")


def _severity_color(severity: str) -> str:
    return {"error": "red", "warning": "orange"}.get(severity, "gray")


def _render_report_group(title: str, entries: list[dict], color: str) -> None:
    if not entries:
        return
    st.markdown(f"**{title}** ({len(entries)})")
    for entry in entries:
        with st.container(border=True):
            top = st.columns([2, 1, 1])
            top[0].markdown(f"**{entry['field']}**")
            top[0].write(entry["value"] if entry["value"] is not None else "—")
            if entry["confidence"] is not None:
                top[1].metric(
                    "confidence", f"{entry['confidence']:.2f}", label_visibility="visible"
                )
            if entry["field_status"]:
                top[2].badge(
                    entry["field_status"],
                    color=color if entry["field_status"] != "extracted" else "gray",
                )
            if entry["source_note"]:
                st.caption(f"source: {entry['source_note']}")
            for flag in entry["flags"]:
                st.badge(
                    f"{flag['severity']}: {flag['reason']}", color=_severity_color(flag["severity"])
                )


if file_path:
    with st.status("Running pipeline...", expanded=False) as status:
        result = run_pipeline(
            {"file_path": file_path, "schema_id": schema_id},
            workers=[extraction_worker, validation_worker, confidence_worker, report_worker],
            correction_worker=correction_worker,
        )
        status.update(
            label="Pipeline finished" if result.status == "ok" else "Pipeline failed",
            state="complete" if result.status == "ok" else "error",
        )

    if result.status == "failed":
        st.error(f"Extraction failed after retries: {result.reason}")
    else:
        document = result.final_state["document"]
        report = result.final_state["report"]
        pages = result.final_state["pages"]

        _render_pipeline_stages(result.history)
        st.divider()

        col_image, col_report = st.columns([1, 1])

        with col_image:
            st.subheader("Source document")
            for page in pages:
                st.image(page.image, width="stretch")

        with col_report:
            st.subheader("Validation report")
            m1, m2, m3 = st.columns(3)
            m1.metric("Errors", len(report["errors"]))
            m2.metric("Warnings", len(report["warnings"]))
            m3.metric("Passed", len(report["pass"]))

            _render_report_group("Errors", report["errors"], "red")
            _render_report_group("Warnings", report["warnings"], "orange")
            with st.expander(f"Pass ({len(report['pass'])})", expanded=False):
                for entry in report["pass"]:
                    line = f"**{entry['field']}**: {entry['value']}"
                    if entry["source_note"]:
                        line += f"  \n_source: {entry['source_note']}_"
                    st.markdown(line)

        st.divider()
        _render_agentic_panel(result.final_state)

        st.divider()
        doc_schema = get_schema(schema_id)
        list_field = get_list_field_name(doc_schema)
        st.subheader(list_field.replace("_", " ").title())
        st.dataframe([item.model_dump() for item in getattr(document, list_field)], width="stretch")

        st.subheader("Export")
        with tempfile.TemporaryDirectory() as export_dir:
            json_path = Path(export_dir) / "result.json"
            csv_path = Path(export_dir) / "result.csv"
            export_json(result.final_state, json_path)
            export_csv(result.final_state, csv_path)

            col_json, col_csv = st.columns(2)
            col_json.download_button(
                "Download JSON",
                data=json_path.read_text(),
                file_name="result.json",
                mime="application/json",
            )
            col_csv.download_button(
                "Download CSV", data=csv_path.read_text(), file_name="result.csv", mime="text/csv"
            )
else:
    st.info("Upload a document, or pick a sample invoice from the dropdown, to get started.")
