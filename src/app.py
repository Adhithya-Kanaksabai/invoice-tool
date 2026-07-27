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

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

# Streamlit Community Cloud exposes secrets via st.secrets, not as OS env
# vars — bridge them into os.environ before any local module import, since
# db.py reads DATABASE_URL at import time (module-level create_engine call).
# No-op locally, where secrets.toml doesn't exist and GEMINI_API_KEY/
# DATABASE_URL already come from .env via python-dotenv instead.
try:
    for _key, _value in st.secrets.items():
        os.environ.setdefault(_key, str(_value))
except Exception:
    pass

from confidence import confidence_worker
from db import init_db
from export import export_csv, export_json
from extract import extraction_worker
from ingest import compute_content_hash
from orchestrator import run_pipeline
from persistence import (
    check_natural_id_exists,
    get_document_review,
    persist_pipeline_result,
    save_document_review,
)
from report import report_worker
from retry import correction_worker
from schema_registry import get_list_field_name, get_scalar_field_names, get_schema
from validate import validation_worker

# Local dev and Docker create the schema via `alembic upgrade head` (see
# Dockerfile's CMD) before app.py ever runs. Streamlit Community Cloud has no
# equivalent pre-start hook — it just runs this file directly — so a fresh
# ephemeral SQLite file there would otherwise have no tables at all.
# init_db()'s create_all() is idempotent (checks existing tables first), so
# this is a safe no-op anywhere Alembic already ran, and the actual fix
# wherever it hasn't.
init_db()

st.set_page_config(page_title="Invoice Intelligence Tool", page_icon="🧾", layout="wide")

# Flat, restrained styling pass (Claude design system direction: flat surfaces,
# hairline borders, no shadows/gradients, 12px card radius, muted secondary
# text) applied over Streamlit's own components via CSS, not a framework swap
# — see D8, this stays Streamlit. Conservative selectors (data-testid, which
# is fairly stable across Streamlit versions) so a selector miss degrades to
# "no visual change" rather than a broken layout.
st.markdown(
    """
    <style>
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-radius: 12px !important;
        box-shadow: none !important;
    }
    [data-testid="stMetric"] {
        background: rgba(127, 127, 127, 0.12);
        border: 1px solid rgba(127, 127, 127, 0.18);
        border-radius: 8px;
        padding: 0.75rem 1rem;
    }
    [data-testid="stMetricLabel"] {
        font-size: 13px;
        opacity: 0.7;
    }
    span[data-testid="stBadge"] {
        border-radius: 999px !important;
        font-weight: 500;
        box-shadow: none !important;
    }
    div[data-testid="stExpander"] details {
        border-radius: 8px;
        box-shadow: none !important;
    }
    button[data-testid="stBaseButton-secondary"],
    button[data-testid="stBaseButton-secondaryFormSubmit"] {
        border-radius: 8px !important;
        box-shadow: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

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

# reviewed_at is stored naive-UTC in the DB (datetime.utcnow()); display it in
# IST, the timezone the reviewer actually works in. IST is a fixed UTC+5:30
# with no daylight saving, so a plain offset is exact — no tz database needed.
IST_OFFSET = timedelta(hours=5, minutes=30)


def _format_ist(when: datetime | None) -> str:
    if when is None:
        return ""
    return f" on {(when + IST_OFFSET):%Y-%m-%d %H:%M} IST"


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

    force_rerun = st.checkbox(
        "Force re-extraction (skip cache)",
        help="Ignore any cached result for this exact file and call the vision model again — "
        "useful when testing a prompt/schema change against a file you've already run.",
    )

with col_upload:
    uploaded = st.file_uploader(
        f"Upload a {doc_type_label.lower()}", type=["pdf", "jpg", "jpeg", "png", "webp"]
    )

file_path: str | None = None
original_filename: str | None = None  # tempfile paths are random — persistence needs the real name
if uploaded:
    with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as tmp:
        tmp.write(uploaded.getvalue())
        file_path = tmp.name
        original_filename = uploaded.name
elif sample_pick != "(none — upload below)":
    file_path = str(SAMPLE_DIR / sample_pick)
    original_filename = sample_pick


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
        if final_state.get("correction_attempted_but_failed"):
            st.badge(
                "Attempted — could not resolve, original values kept", icon="⚠️", color="orange"
            )
            reason = final_state.get("correction_failure_reason")
            if reason:
                st.caption(reason)
            return
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


def _render_validation_report(report: dict) -> None:
    """The three-signal report (errors / warnings / pass), rendered inside the
    right-hand 'Validation report' tab next to the source image."""
    m1, m2, m3 = st.columns(3)
    m1.metric("Errors", len(report["errors"]))
    m2.metric("Warnings", len(report["warnings"]))
    m3.metric("Passed", len(report["pass"]))
    st.caption("Only errors trigger automatic correction — warnings are informational.")

    _render_report_group("Errors", report["errors"], "red")
    _render_report_group("Warnings", report["warnings"], "orange")
    with st.expander(f"Pass ({len(report['pass'])})", expanded=False):
        for entry in report["pass"]:
            line = f"**{entry['field']}**: {entry['value']}"
            if entry["source_note"]:
                line += f"  \n_source: {entry['source_note']}_"
            st.markdown(line)


def _flagged_field_names(report: dict) -> set[str]:
    """Fields that carry an error or warning — the ones a reviewer should look
    at first. Derived from the already-built report, not recomputed."""
    return {entry["field"] for entry in report["errors"] + report["warnings"]}


def _render_review_section(schema_id: str, document, report: dict, document_id: int | None) -> None:
    """
    The human-in-the-loop step: let a reviewer correct any field the model got
    wrong and approve — the tool's actual promise ("confirm the flagged fields
    instead of retyping"), which until now the UI only displayed but couldn't
    act on.

    The model's own output (document) is never mutated here — edits are
    validated back through the SAME schema the extractor uses, then saved as
    corrected_data ALONGSIDE the original (see persistence.save_document_review
    and models.py). Approving with no edits stores nothing but the approval.

    Rendered inside the right-hand "Review & approve" tab (see the main layout),
    next to the source image so a reviewer can read the document while
    correcting fields — hence no subheader of its own here, the tab names it.
    """
    if document_id is None:
        st.warning(
            "This result couldn't be saved to the database, so there's nothing to attach a "
            "review to. See the error above."
        )
        return

    doc_schema = get_schema(schema_id)
    scalar_fields = get_scalar_field_names(doc_schema)
    list_field = get_list_field_name(doc_schema)
    flagged = _flagged_field_names(report)

    review = st.session_state.get(f"review_{document_id}")
    if review and review.get("review_status") == "approved":
        edited_suffix = "with edits" if review.get("corrected_data") else "as-is"
        when_str = _format_ist(review.get("reviewed_at"))
        st.success(f"Approved ({edited_suffix}){when_str}. You can edit and re-approve below.")

    # Prefill from a prior human correction if one exists, else the model's
    # own output — always start the editor from the best-known-truth so far.
    base = (review or {}).get("corrected_data") or document.model_dump(mode="json")

    st.caption(
        "Edit any value the model got wrong, then approve. The model's original output is always "
        "kept — your corrections are stored alongside it, never over it."
    )

    with st.form(key=f"review_form_{document_id}"):
        edited_scalars: dict[str, str] = {}
        cols = st.columns(2)
        for i, name in enumerate(scalar_fields):
            current = base.get(name)
            label = name.replace("_", " ").title()
            if name in flagged:
                label = f"⚠️ {label}"
            edited_scalars[name] = cols[i % 2].text_input(
                label,
                value="" if current is None else str(current),
                key=f"edit_{document_id}_{name}",
            )

        st.markdown(f"**{list_field.replace('_', ' ').title()}**")
        edited_items = st.data_editor(
            base.get(list_field, []),
            key=f"edit_items_{document_id}",
            num_rows="fixed",
            width="stretch",
        )

        submitted = st.form_submit_button("✅ Approve", type="primary")

    if not submitted:
        return

    # Build the candidate document from the model's full output, override the
    # editable fields, and re-validate through the schema — a human's
    # corrections have to form a valid document too, same bar as extraction.
    candidate = document.model_dump(mode="json")
    for name in scalar_fields:
        raw = edited_scalars[name].strip()
        candidate[name] = raw or None
    candidate[list_field] = list(edited_items)

    try:
        validated = doc_schema.model.model_validate(candidate)
    except ValidationError as e:
        bad = sorted({".".join(str(p) for p in err["loc"]) for err in e.errors()})
        st.error(
            "Can't approve — these fields aren't valid after your edits: "
            f"{', '.join(bad)}. Fix them and approve again."
        )
        return

    # corrected_data is stored only if the human's final answer actually
    # differs from what the model produced — otherwise it's an approval of the
    # model's output as-is, and there's nothing to store but the approval.
    validated_dump = validated.model_dump(mode="json")
    original_dump = document.model_dump(mode="json")
    corrected = None if validated_dump == original_dump else validated_dump

    try:
        save_document_review(document_id, corrected)
    except Exception as e:
        st.error(f"Couldn't save the review: {e}")
        return

    st.session_state[f"review_{document_id}"] = get_document_review(document_id)
    if corrected is None:
        st.success("Approved — the model's output was confirmed correct, no changes stored.")
    else:
        st.success("Approved — your corrections were saved alongside the model's original output.")


if file_path:
    # Streamlit re-runs this whole script on every widget interaction. Without
    # this guard, editing a field or clicking Approve would silently re-run the
    # entire (live, paid) pipeline and write a duplicate run row every time.
    # Key the cached result by the file's content + schema + force-rerun flag,
    # so the pipeline runs exactly once per distinct input, and review-widget
    # interactions reuse the stored result. (Also fixes a latent re-run-on-
    # every-interaction bug that predates the review loop.)
    content_hash = compute_content_hash(file_path)
    run_key = f"{content_hash}|{schema_id}|{force_rerun}"

    if st.session_state.get("run_key") != run_key:
        started_at = datetime.utcnow()
        with st.status("Running pipeline...", expanded=False) as status:
            result = run_pipeline(
                {
                    "file_path": file_path,
                    "schema_id": schema_id,
                    "duplicate_checker": check_natural_id_exists,
                    "skip_cache": force_rerun,
                },
                workers=[
                    extraction_worker,
                    validation_worker,
                    confidence_worker,
                    report_worker,
                ],
                correction_worker=correction_worker,
            )
            status.update(
                label="Pipeline finished" if result.status == "ok" else "Pipeline failed",
                state="complete" if result.status == "ok" else "error",
            )

        # Persistence is the system of record, not an optional signal — a save
        # failure is surfaced loudly, not swallowed (see persistence.py). It
        # doesn't block showing the result below: the extraction itself is real
        # and already succeeded even if the DB write just failed.
        document_id: int | None = None
        persist_error: str | None = None
        try:
            document_id = persist_pipeline_result(
                result,
                original_filename=original_filename or Path(file_path).name,
                content_hash=content_hash,
                started_at=started_at,
            )
        except Exception as e:
            persist_error = str(e)

        st.session_state["run_key"] = run_key
        st.session_state["result"] = result
        st.session_state["document_id"] = document_id
        st.session_state["persist_error"] = persist_error
        st.session_state[f"review_{document_id}"] = (
            get_document_review(document_id) if document_id else None
        )

    result = st.session_state["result"]
    document_id = st.session_state["document_id"]
    persist_error = st.session_state["persist_error"]

    if persist_error:
        st.error(
            f"Extraction succeeded but saving this result to the database failed: {persist_error}"
        )

    if result.status == "failed":
        st.error(result.reason or "Extraction failed.")
    else:
        document = result.final_state["document"]
        report = result.final_state["report"]
        pages = result.final_state["pages"]

        if result.final_state.get("reused_from_cache"):
            st.info(
                "This exact file was already extracted in a prior run — reused that result "
                "instead of calling the vision model again.",
                icon="♻️",
            )

        _render_pipeline_stages(result.history)
        st.divider()

        # Source image on the LEFT, the things you do WITH it on the right, as
        # tabs — so a reviewer reads the document and corrects fields side by
        # side (D10: the image is always next to the output). Review is the
        # first tab because acting on the extraction is the primary job here;
        # the validation report that explains the flags is one tab over.
        col_image, col_work = st.columns([1, 1])

        with col_image:
            st.subheader("Source document")
            for page in pages:
                st.image(page.image, width="stretch")

        with col_work:
            n_flags = len(report["errors"]) + len(report["warnings"])
            review_tab, report_tab = st.tabs(
                [
                    "📝 Review & approve",
                    f"✅ Validation report ({n_flags} flagged)"
                    if n_flags
                    else "✅ Validation report",
                ]
            )
            with review_tab:
                _render_review_section(schema_id, document, report, document_id)
            with report_tab:
                _render_validation_report(report)

        st.divider()
        _render_agentic_panel(result.final_state)

        st.divider()
        st.subheader("Export")
        st.caption(
            "Exports the model's original extraction. Human corrections are saved to the database."
        )
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
