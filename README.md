# Invoice Intelligence Tool

![Python 3.11](https://img.shields.io/badge/python-3.11-blue) ![License: MIT](https://img.shields.io/badge/license-MIT-green) ![Tests: 81 passing](https://img.shields.io/badge/tests-81%20passing-brightgreen)

Extracts structured, validated data from scanned invoices and receipts (PDF or image) and
surfaces exactly what a human needs to review — missing fields, arithmetic inconsistencies,
low-confidence extractions — instead of asking anyone to trust an LLM's output blindly.

![Main extraction view](docs/screenshots/main-extraction-view.png)

## Problem it solves

Manual invoice data entry in accounts-payable is slow and error-prone: someone reads a scanned
invoice and retypes vendor, dates, line items, and totals by hand. This tool automates the
transcription and turns the human's job into "confirm these three flagged fields," not "retype
the whole document."

The core constraint driving every decision below: **the LLM is never blindly trusted.**
Structured, layered validation is the actual subject of this project, not a UI wrapped around an
API call.

## Architecture

One **generic orchestrator** (`orchestrator.py`) runs a list of **workers** — invoices were the
first workflow built on it, receipts are the second, registered on the exact same engine. Every
worker is `dict -> WorkerResult{status, state, reason}`; the orchestrator only ever reads
`status`. It never imports `Invoice` or `Receipt`, never knows what a subtotal is, never contains
a business rule.

```
Ingest (PDF/image -> page images)
        |
        v
Extract  (Gemini Vision)  --------------------------+
        |                                           |
        v                                           |
Validate (schema, then business rules)               |
        |                                            |
   error flag?  --yes-->  Correction Worker  --------+   (one bounded agentic loop,
        | no                (re-examine, re-validate)     capped at 1 round)
        v
Score confidence  ->  Build report  ->  Persist (DB)  ->  Export (JSON/CSV)  ->  Streamlit UI
```

A `schema_registry.py` maps a `schema_id` (`"invoice-v1"`, `"receipt-v1"`) to its Pydantic model,
business rules, retry groups, and required fields — nothing in the generic path (orchestrator,
structural validation, confidence scoring) hardcodes a document type by name. Adding the second
schema required **zero changes** to that generic path, and is the actual proof the architecture
generalizes, not just a claim about it — see [`GOD_FILE.md`](GOD_FILE.md) for the three real bugs
that second schema surfaced.

**Only one step is agentic, deliberately.** Extraction is one deterministic call. Validation is
pure rule-checking — an LLM must never decide whether `subtotal + tax == total`. The Correction
Worker is the one place "how should I re-examine this" has no single correct fixed procedure, so
it gets a tool-calling loop, bounded by a hard turn cap plus a hard cap on how many times the
orchestrator invokes it per run — "decides for itself" never becomes "loops forever."

Every run is persisted (SQLAlchemy + Alembic) — not just shown once and discarded. That backs a
content-hash extraction cache (re-uploading the same file skips a redundant Gemini call) and
cross-run duplicate detection (an invoice number that already exists from a *previous* session).

For the full decision log (D1–D17: why two validation layers, why confidence is computed instead
of asked, why citation-level grounding instead of bounding boxes, the schema-mismatch bug that
would have zeroed out the eval numbers, and more), see [`spec/design.md`](spec/design.md). For the
plain-language version with real incidents and anticipated interview Q&A, see
[`GOD_FILE.md`](GOD_FILE.md).

## Evaluation

`src/eval.py` runs the full pipeline over a hand-verified test set and compares against ground
truth, generalized to run over both registered schemas.

```
Test set: 29 hand-verified documents (24 invoices, 5 receipts) — spanning multiple visual
templates, several currencies, real-world phone-photo receipts and web-sourced invoice
templates (not just synthetically degraded PDFs).

Overall extraction success rate: 100.0%  (29/29)
Overall field-level accuracy:    99.1%

invoice-v1: extraction success 100.0%  field accuracy 99.6%  (24/24)
receipt-v1: extraction success 100.0%  field accuracy 96.8%  (5/5)
```

**Honest caveat:** 29 hand-curated documents is a correctness signal on the specific range of
formats/degradations actually tested, not a claim of general robustness. Every miss traced to a
specific, understood cause (see `GOD_FILE.md`) — none were unexplained noise.

An independent OCR cross-check was built, measured, and **removed** after it scored a 0% catch
rate on real errors across 308 field observations — see `spec/design.md` / `GOD_FILE.md` for why,
and why that "no" was worth keeping documented rather than discarded.

![Validation report](docs/screenshots/validation-report.png)

## Limitations

- Test set (29 documents) is hand-curated, not a standard public benchmark.
- No bounding-box grounding — citation-level text notes only (`source_note` per field).
- Single invoice per file; no multi-invoice-per-file support.
- No jurisdiction-specific rules (GST/VAT number/rate validation) beyond generic tax-as-a-field.
- Docker/docker-compose packaging is planned but not yet built (see `spec/design.md`'s roadmap).

## Tech stack

Python 3.11, Pydantic v2, `pdf2image` + Poppler, Pillow, `google-genai` (Gemini), Streamlit,
SQLAlchemy + Alembic, pandas. Dev/test-only: Ruff, pytest, `reportlab` (test-data generation).

## Running it

```bash
cd invoice-tool
python -m venv venv
venv/Scripts/pip install -r requirements.txt   # also needs Poppler on PATH
# create .env with GEMINI_API_KEY=your-key-here
venv/Scripts/streamlit run src/app.py
```

Run the eval suite: `venv/Scripts/python src/eval.py`

Run the unit tests (no API key needed, pure logic only):
`venv/Scripts/pip install -r requirements-dev.txt && venv/Scripts/pytest tests/unit`

Regenerate the synthetic test set:
`venv/Scripts/pip install -r requirements-test.txt && venv/Scripts/python tests/generate_sample_invoices.py`

![Agentic Correction Worker panel](docs/screenshots/agentic-correction-panel.png)

## License

[MIT](LICENSE)
