"""
extract.py — the Extraction Worker.

One deterministic call: page images in, a schema-conforming Pydantic instance
out. No decision-making happens here — see design.md "The one agentic loop"
for why extraction is NOT the agentic step (that's retry.py).

Schema-agnostic mechanism, schema-specific prompt content: the prompt is built
from schema_registry.get_schema(schema_id).model's JSON schema, not a
hardcoded Invoice import (per D15) — but per D15's own reasoning, prompt
*quality* is inherently schema-specific, so the generic mechanism here is
"embed whatever model is registered," not "know what an invoice is."

Two distinct failure modes, handled differently (FR7a / D9):
- Hard extraction failure (API error, timeout, response that isn't parseable
  or doesn't validate against the schema at all) -> retry-with-backoff here,
  capped at 2 retries. If still failing, WorkerResult(status="failed") so the
  orchestrator stops cleanly for this invoice and eval.py can skip it.
- Values that parse fine but are wrong/inconsistent -> NOT this module's
  problem. That's schema_validate.py / business_validate.py / retry.py.
"""

from __future__ import annotations

import base64
import json
import os
import time

from dotenv import load_dotenv
from google import genai
from google.genai import types

from ingest import PageImage, load_page_images
from orchestrator import WorkerResult
from schema_registry import get_schema

load_dotenv()  # picks up GEMINI_API_KEY from a .env file if present, no-op otherwise

MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
MAX_ATTEMPTS = 3  # 1 initial + 2 retries, per FR7a
BACKOFF_SECONDS = 2.0

EXTRACTION_INSTRUCTIONS = """\
You are extracting structured data from a scanned document (one or more page
images are attached) into JSON that validates against the JSON Schema below.

For every scalar field the schema defines under "properties" (i.e. not
line_items, not field_status, not source_note themselves), also populate two
side maps, keyed by that field's name:

- `field_status`: one of "extracted", "missing", "ambiguous", "unreadable".
  Use "missing" only if the field is genuinely absent from the document. Use
  "ambiguous" if multiple candidate values exist and you're not sure which is
  correct (still give your best-guess value). Use "unreadable" if the region
  exists but is illegible (still give your best-guess value). Do not guess
  silently instead of using these statuses when uncertain.
- `source_note`: a short text description of where on the page you read the
  value (e.g. "table row 3", "top-right header block").

Do not invent a confidence number — none is requested.
Respond with ONLY the raw JSON object. No markdown code fences, no commentary.

JSON Schema:
{schema_json}
"""


def _build_prompt(schema_id: str) -> str:
    doc_schema = get_schema(schema_id)
    schema_json = json.dumps(doc_schema.model.model_json_schema(), indent=2)
    return EXTRACTION_INSTRUCTIONS.format(schema_json=schema_json)


def _image_parts(pages: list[PageImage]) -> list[types.Part]:
    return [
        types.Part.from_bytes(data=base64.b64decode(page.b64_png), mime_type="image/png")
        for page in pages
    ]


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop opening fence (``` or ```json)
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _call_gemini(client: genai.Client, prompt: str, pages: list[PageImage]) -> str:
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[prompt, *_image_parts(pages)],
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    if not response.text:
        raise ValueError("empty response from vision model")
    return response.text


def extraction_worker(state: dict) -> WorkerResult:
    """
    Reads state["file_path"] and state["schema_id"], runs the Extraction
    Worker, and returns WorkerResult with state["document"] (a validated
    instance of whichever model is registered under schema_id — Invoice,
    Receipt, or a future schema) and state["pages"] (the source page images,
    kept for the UI and for retry.py to reuse without re-ingesting).
    """
    schema_id = state["schema_id"]
    doc_schema = get_schema(schema_id)
    prompt = _build_prompt(schema_id)

    pages = state.get("pages") or load_page_images(state["file_path"])
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw_text = _call_gemini(client, prompt, pages)
            raw = json.loads(_strip_code_fences(raw_text))
            document = doc_schema.model.model_validate(raw)
            return WorkerResult(
                status="ok",
                state={**state, "pages": pages, "document": document},
            )
        except Exception as e:  # API error, bad JSON, or ValidationError — all hard failures here
            last_error = e
            if attempt < MAX_ATTEMPTS:
                time.sleep(BACKOFF_SECONDS * attempt)

    return WorkerResult(
        status="failed",
        state={**state, "pages": pages, "extraction_failed": True},
        reason=f"extraction failed after {MAX_ATTEMPTS} attempts: {last_error}",
    )
