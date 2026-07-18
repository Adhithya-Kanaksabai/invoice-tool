"""
app.py — Streamlit UI (T14/T14a).

Per D8: Streamlit, not React — this project demonstrates AI systems
engineering, not frontend engineering. Per D10: the source image is always
shown next to the output (st.columns([1, 1])) — without this, a human can't
actually verify an extraction. Per D14, each field's source_note is shown
next to its value — the citation-level grounding this project ships instead
of pixel-level bounding boxes.
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
from validate import validation_worker

st.set_page_config(page_title="Invoice Intelligence Tool", layout="wide")
st.title("Invoice Intelligence Tool")
st.caption("Upload a scanned invoice (PDF or image) to extract and validate its fields.")

uploaded = st.file_uploader("Upload an invoice", type=["pdf", "jpg", "jpeg", "png", "webp"])

if uploaded:
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as tmp:
        tmp.write(uploaded.getvalue())
        tmp_path = tmp.name

    with st.spinner("Extracting and validating..."):
        result = run_pipeline(
            {"file_path": tmp_path, "schema_id": "invoice-v1"},
            workers=[extraction_worker, validation_worker, confidence_worker, report_worker],
            correction_worker=correction_worker,
        )

    if result.status == "failed":
        st.error(f"Extraction failed after retries: {result.reason}")
    else:
        invoice = result.final_state["invoice"]
        report = result.final_state["report"]
        pages = result.final_state["pages"]

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

            def render_group(title: str, entries: list[dict]) -> None:
                if not entries:
                    return
                st.markdown(f"**{title}**")
                for entry in entries:
                    line = f"**{entry['field']}**: {entry['value']}"
                    if entry["source_note"]:
                        line += f"  \n_source: {entry['source_note']}_"
                    st.markdown(line)
                    details = []
                    if entry["confidence"] is not None:
                        details.append(f"confidence {entry['confidence']:.2f}")
                    if entry["field_status"]:
                        details.append(f"status: {entry['field_status']}")
                    for flag in entry["flags"]:
                        details.append(f"{flag['severity']} ({flag['layer']}): {flag['reason']}")
                    if details:
                        st.caption(" | ".join(details))

            render_group("Errors", report["errors"])
            render_group("Warnings", report["warnings"])
            render_group("Pass", report["pass"])

        st.subheader("Line items")
        st.table([li.model_dump() for li in invoice.line_items])

        st.subheader("Export")
        with tempfile.TemporaryDirectory() as export_dir:
            json_path = Path(export_dir) / "result.json"
            csv_path = Path(export_dir) / "result.csv"
            export_json(result.final_state, json_path)
            export_csv(result.final_state, csv_path)

            col_json, col_csv = st.columns(2)
            col_json.download_button(
                "Download JSON", data=json_path.read_text(), file_name="result.json", mime="application/json"
            )
            col_csv.download_button(
                "Download CSV", data=csv_path.read_text(), file_name="result.csv", mime="text/csv"
            )
