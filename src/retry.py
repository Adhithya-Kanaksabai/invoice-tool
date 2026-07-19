"""
retry.py — the Correction Worker (T9), the one agentic loop in this project.

Per D11: everywhere else in this pipeline is deterministic on purpose.
Correction is the one place a genuine judgment call exists — "given this
validation failure, re-examine and decide when you're satisfied" doesn't have
a single correct procedure to hardcode, unlike validation (which must never
let an LLM decide whether subtotal + tax == total).

The retry FIELD GROUP itself is still deterministic (D6/D16) — each schema's
own RETRY_GROUPS dict (business_validate.py's for invoices,
business_validate_receipt.py's for receipts), looked up generically via
doc_schema.retry_groups (schema_registry.py) so this worker never imports a
schema-specific module and never knows "invoice" by name. What's agentic is
HOW the model re-examines the group and decides it's done: it gets one tool,
`reexamine`, to explicitly re-look before committing via a second tool,
`submit_correction` — bounded by MAX_TOOL_TURNS so "decides for itself when to
stop" doesn't become "loops forever" (this is the code-enforced ceiling D11
calls for, separate from and in addition to orchestrator.py's own
max_correction_rounds=1 cap on how many times the ORCHESTRATOR invokes this
worker per pipeline run).

Time-boxed per tasks.md T9: if the tool-calling loop doesn't converge (model
never calls submit_correction, or produces something that doesn't validate)
within MAX_TOOL_TURNS turns, falls back to a deterministic single-shot
re-extraction of just the retry group — still real, still correct, and the
tradeoff design.md's D6 anticipated in advance, not a silent downgrade.
"""

from __future__ import annotations

import base64
import json
import os
import re

from dotenv import load_dotenv
from google import genai
from google.genai import types

from ingest import PageImage
from orchestrator import WorkerResult
from schema_registry import get_schema

load_dotenv()

MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
MAX_TOOL_TURNS = 4

REEXAMINE_DECL = types.FunctionDeclaration(
    name="reexamine",
    description=(
        "Call this if you want to explicitly look at the attached image "
        "again before committing to corrected values — e.g. to re-check a "
        "specific region for a value you're not fully confident about yet. "
        "You may call this more than once, but you must eventually call "
        "submit_correction to finish."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "fields": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        },
        "required": ["fields", "reason"],
    },
)

SUBMIT_DECL = types.FunctionDeclaration(
    name="submit_correction",
    description=(
        "Call this exactly once, when you have decided on final corrected "
        "values for every field in the retry group. This ends the correction."
    ),
    parameters_json_schema={
        "type": "object",
        "properties": {
            "fields": {
                "type": "object",
                "description": "field name -> corrected value, for every field in the retry group",
            },
            "note": {"type": "string", "description": "one-line rationale for the correction"},
        },
        "required": ["fields", "note"],
    },
)


def _expand_retry_fields(flags, retry_groups: dict[str, list[str]]) -> set[str]:
    """
    Which fields need re-extraction, expanded to full dependency groups for
    arithmetic flags (per D6) — driven by whichever schema's retry_groups
    dict the registry hands back, not a hardcoded invoice-specific import.
    """
    fields: set[str] = set()
    for f in flags:
        if f.severity != "error":
            continue
        fields.update(retry_groups.get(f.field, [f.field]))
    return fields


def _failure_reason_text(flags, retry_fields: set[str]) -> str:
    relevant = [f for f in flags if f.severity == "error"]
    lines = "\n".join(f"- {f.field}: {f.reason}" for f in relevant)
    return (
        "Validation found the following problem(s) with a prior extraction "
        f"of this document:\n{lines}\n\n"
        f"Re-extract EXACTLY these fields, together, from the attached "
        f"image: {sorted(retry_fields)}. They form one dependency group — "
        "the actual error could be in any one of them (or more than one), "
        "not necessarily the field named in the problem above."
    )


def _image_parts(pages: list[PageImage]) -> list[types.Part]:
    return [
        types.Part.from_bytes(data=base64.b64decode(p.b64_png), mime_type="image/png")
        for p in pages
    ]


_CURRENCY_CHARS = re.compile(r"[^0-9.\-]")


def _is_float_field(field_name: str, model) -> bool:
    import typing

    field_info = model.model_fields.get(field_name)
    if field_info is None:
        return False
    annotation = field_info.annotation
    return annotation is float or float in typing.get_args(annotation)


def _coerce_numeric_strings(field_name: str, value, model) -> object:
    """
    The model's free-form tool-call args aren't type-enforced the way the
    extraction prompt's response_mime_type=application/json is — it sometimes
    returns numeric fields as currency strings (e.g. "$606.34"). Coerce those
    back to float before merging, based on the field's declared annotation,
    rather than let Pydantic silently reject the whole correction.
    """
    if not isinstance(value, str) or not _is_float_field(field_name, model):
        return value
    cleaned = _CURRENCY_CHARS.sub("", value)
    try:
        return float(cleaned)
    except ValueError:
        return value


def _deterministic_fallback(
    client: genai.Client, prompt_text: str, pages: list[PageImage], retry_fields: set[str]
) -> dict | None:
    """D6's documented fallback: one plain JSON re-extraction call, no tools."""
    fallback_prompt = (
        prompt_text + "\n\nRespond with ONLY a JSON object mapping each of these field "
        f"names to its corrected value: {sorted(retry_fields)}. No markdown "
        "fences, no commentary."
    )
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[fallback_prompt, *_image_parts(pages)],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    try:
        return json.loads((response.text or "").strip())
    except json.JSONDecodeError:
        return None


def correction_worker(state: dict) -> WorkerResult:
    """
    Called by orchestrator.run_pipeline when the Validation Worker returns
    status="retry". Re-extracts just the failed dependency group (D6),
    letting the model decide, via tool-calling, how much re-examination it
    needs before committing — bounded, per D11.
    """
    schema_id = state["schema_id"]
    doc_schema = get_schema(schema_id)
    document = state["document"]
    pages = state["pages"]
    flags = state.get("flags", [])

    retry_fields = _expand_retry_fields(flags, doc_schema.retry_groups)
    if not retry_fields:
        return WorkerResult(status="ok", state=state)

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt_text = _failure_reason_text(flags, retry_fields)

    contents: list = [prompt_text, *_image_parts(pages)]
    tool = types.Tool(function_declarations=[REEXAMINE_DECL, SUBMIT_DECL])
    config = types.GenerateContentConfig(tools=[tool])

    corrected_fields: dict | None = None
    note = ""

    for _ in range(MAX_TOOL_TURNS):
        response = client.models.generate_content(
            model=MODEL_NAME, contents=contents, config=config
        )
        calls = response.function_calls
        if not calls:
            break  # model didn't use the tool protocol — fall through to the deterministic fallback

        contents.append(
            response.candidates[0].content
        )  # the model's turn, incl. function_call parts

        response_parts = []
        submitted = False
        for call in calls:
            if call.name == "submit_correction":
                corrected_fields = call.args.get("fields", {})
                note = call.args.get("note", "")
                submitted = True
            elif call.name == "reexamine":
                response_parts.append(
                    types.Part.from_function_response(
                        name="reexamine",
                        response={
                            "result": "Noted — the attached image is unchanged; look again and "
                            "call submit_correction with your final values when ready."
                        },
                    )
                )

        if submitted:
            break
        if response_parts:
            contents.append(types.Content(role="user", parts=response_parts))

    used_fallback = corrected_fields is None
    if used_fallback:
        corrected_fields = _deterministic_fallback(client, prompt_text, pages, retry_fields)

    if not corrected_fields:
        # Even the fallback didn't produce anything usable — leave the
        # document as-is. Re-validation downstream will surface the same
        # error flags, and since orchestrator.py's max_correction_rounds=1
        # caps how many times this worker runs per pipeline pass, the result
        # is "unresolved for human review," not an infinite loop (per D6).
        return WorkerResult(status="ok", state=state)

    merged_raw = document.model_dump(mode="json")
    for field in retry_fields:
        if field in corrected_fields:
            merged_raw[field] = _coerce_numeric_strings(
                field, corrected_fields[field], doc_schema.model
            )

    try:
        corrected_document = doc_schema.model.model_validate(merged_raw)
    except Exception as e:
        # Corrected values didn't even type-check after coercion — same
        # "leave as-is, surface as unresolved" reasoning as above, but keep
        # the reason visible for debugging rather than swallowing it.
        return WorkerResult(
            status="ok",
            state=state,
            reason=f"correction produced invalid data, kept original: {e}",
        )

    retried_fields = state.get("retried_fields", set()) | retry_fields
    return WorkerResult(
        status="ok",
        state={
            **state,
            "document": corrected_document,
            "retried_fields": retried_fields,
            "correction_note": note,
            "correction_used_fallback": used_fallback,
        },
    )
