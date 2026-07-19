# Invoice Intelligence Tool

Extracts structured, validated data from scanned invoices (PDF or image) and
surfaces exactly what a human needs to review — missing fields, arithmetic
inconsistencies, low-confidence extractions — instead of asking anyone to
trust an LLM's output blindly.

## Problem statement

Manual invoice data entry in accounts-payable is slow and error-prone: a
human reads a scanned invoice and retypes vendor, dates, line items, and
totals by hand. This tool automates the extraction and turns the review step
into "confirm these three flagged fields" instead of "retype the whole
document."

The core engineering constraint driving every decision below: **the LLM is
never blindly trusted.** Structured validation is the actual subject of this
project, not a UI wrapped around an API call.

## Architecture

```
Invoice (PDF/image)
      |
      v
Ingest & normalize -> list of page images        (ingest.py)
      |
      v
Vision LLM extraction -> raw structured output    (extract.py)
      |
      v
Schema validation (structural)                    (schema_validate.py)
      |
      v
Business rule validation (domain)                 (business_validate.py)
      |
      v
Confidence scoring (heuristic, derived)            (confidence.py)
      |
+-----+------------------------------+
| any field failed business rules?   |
v yes                                v no
Correction Worker (agentic,          (skip)
capped at 1 round)  (retry.py)       |
      |                              |
      +-----------+------------------+
                  |
                  v
          Validation report          (report.py)
                  |
                  v
            Export (JSON + CSV)      (export.py)
```

### The orchestrator/worker contract

The pipeline is one **generic orchestrator** (`orchestrator.py`) running
**domain-specific workers**, with invoices as the first workflow on top of
it. Every worker is `dict -> WorkerResult{status, state, reason}`; the
orchestrator only ever reads `status` — it never imports `Invoice`, never
knows what "subtotal" means, and never contains a business rule.

```python
@dataclass
class WorkerResult:
    status: str        # "ok" | "retry" | "failed"
    state: dict         # opaque to the orchestrator — worker-defined shape
    reason: str | None = None
```

Why this matters: the reusable surface is deliberately narrow — the
**contract**, not the workers themselves. `extract.py`, `validate.py`,
`retry.py`, and `report.py` stay honestly invoice-specific internally. A
second document workflow (a receipt, a PO) would get its own workers; only
the shape of the handoff (`WorkerResult`) is shared. This is also exactly
the seam that would let a later move to LangGraph (whose nodes are also
"take state, return state") happen without having paid for LangGraph now.

No config system, no plugin registry, no workflow-definition DSL was built —
those would be generalizing for a second workflow that doesn't exist yet.

### Schema registry — the one place a document type is looked up

`schema_registry.py` maps a `schema_id` (e.g. `"invoice-v1"`) to its Pydantic
model, business rules, retry groups, and required fields. Before this
existed, `Invoice` was imported by name in three different files; adding a
second document type meant editing all three. Now `schema_validate.py` is
fully schema-agnostic — it never imports `Invoice` — and `validate.py` and
`confidence.py` pull business rules and field lists from the registry rather
than hardcoding invoice knowledge. To add a second document type: write its
Pydantic model, its business rule functions (same shape), its retry groups,
register one entry. Nothing in the generic path changes.

Explicitly **not** built: a generic field-type wrapper over Pydantic
(Pydantic already is that layer), a business-rule DSL (a rule syntax generic
enough for "line items sum to subtotal" *and* "PO number matches a contract"
would be its own multi-week project), a generic prompt-builder (prompt
quality is schema-specific — nothing to generalize from a sample size of
one).

**This claim is now demonstrated, not just asserted (D17).** A second
document type, `receipt-v1`, is registered alongside `invoice-v1` —
deliberately a different shape (`Receipt` has `merchant_name`/`tip`/
`payment_method`, no `due_date`/`customer_name`; its own independent business
rules in `business_validate_receipt.py`, never calling into the invoice
rules). Adding it required **zero changes** to `orchestrator.py` or
`schema_validate.py`'s generic path — but it did surface three real
genericity bugs that had been hiding because only one schema had ever been
registered:

- `retry.py` imported `business_validate.retry_field_groups` directly by
  name, instead of going through `doc_schema.retry_groups` — would have
  silently used invoice retry groups for a receipt.
- `validate.py` hardcoded a `seen_invoice_numbers` kwarg when calling every
  business rule — the receipt's duplicate-check rule expects `seen_ids`
  (its own field is `transaction_id`, not `invoice_number`); the mismatched
  kwarg would've been silently absorbed by the rule's `**_` catch-all and
  the check would never fire.
- `report.py` and `confidence.py` both excluded the field `"line_items"` *by
  name* to find the list-typed field — receipt's list field is `"items"`.
  Fixed by detecting the list-typed field generically (by Pydantic
  annotation, via a new `schema_registry.get_scalar_field_names` /
  `get_list_field_name` helper), not by name, in either module.

All three were only findable by actually building a second schema and
running it — which is exactly the point of doing this instead of leaving the
reusability claim untested.

## Schema design

```python
class Invoice(BaseModel):
    vendor_name: str
    customer_name: str          # the "Bill To" party
    invoice_number: str
    invoice_date: date
    due_date: Optional[date] = None
    currency: str = "USD"

    line_items: list[LineItem]

    subtotal: float
    discount: Optional[float] = None
    shipping: Optional[float] = None
    tax: Optional[float] = None
    total: float

    field_status: dict[str, FieldStatus] = {}   # extracted/missing/ambiguous/unreadable
    source_note: dict[str, str] = {}             # citation-level grounding
```

**A real schema mismatch, caught by actually checking the test data (not
assumed from the spec).** The original schema assumed every invoice has a
`tax` field and that `subtotal + tax = total`. The 5 real sample invoices —
a generated SuperStore template — never show tax at all; they show
`shipping` (always) and `discount` (sometimes). Under the original schema,
`check_total_arithmetic` would have flagged every single invoice in the test
set as a business-rule error, regardless of whether the extraction was
correct — silently invalidating the eval numbers below.

Fix: `discount`, `shipping`, and `tax` are all optional (absent = 0), and the
arithmetic check generalizes to `subtotal - discount + shipping + tax =
total`, which reduces to the original `subtotal + tax = total` when
discount/shipping are absent. This is a strict generalization, not a
narrowing to one invoice format — see `spec/design.md` D16 for the full
writeup, including the corresponding widening of the `total` retry group.

## Validation strategy — two layers, kept separate on purpose

- **Schema validation** (`schema_validate.py`) — is this well-formed? Types,
  required fields present, dates/numbers parseable. Mostly Pydantic's own
  job; this module turns `ValidationError` into `Flag` objects.
- **Business validation** (`business_validate.py`) — is this *correct*, given
  domain rules? Line items sum to subtotal, `subtotal - discount + shipping +
  tax = total` (both within $0.01 tolerance), invoice date ≤ due date,
  duplicate invoice number within the batch.

These are kept as separate functions, not merged, because they're different
failure classes with different retry implications — "malformed value" and
"domain-inconsistent value" need different handling, and conflating them
would make it impossible to answer which one happened (a question any
interviewer will ask).

**Confidence is heuristic, never LLM-self-reported.** An LLM's own stated
confidence isn't calibrated to anything real. `confidence.py` derives a
score per field from actual signals: did it need a retry, did it pass
business validation. Threshold: 0.7.

**Three signals stay visibly separate** (`field_status`, confidence,
business validation) rather than collapsing into one pass/fail bit — a field
can be confidence-flagged without a business-rule error, or vice versa, and
the report (`report.py`) shows all three independently per field.

## The one agentic loop, and why nowhere else

Every step was evaluated for whether genuine agentic behavior (the model
deciding what to do next) would help, versus just adding buzzword surface:

- **Extraction** — one deterministic call, image in, schema out. Not agentic.
- **Validation** — pure rule-checking. An LLM must never decide whether
  `subtotal + tax == total`. Deliberately non-agentic.
- **Report / Export** — formatting only.
- **Correction (`retry.py`) — the one place it earns its keep.** "Given this
  specific validation failure, figure out what to re-examine and how" is a
  genuine judgment call with no single correct procedure to hardcode. The
  retry **field group** itself is still deterministic (a `subtotal` mismatch
  re-extracts `{line_items, subtotal}` together, since the error could be in
  either) — what's agentic is *how* the model re-examines that group. It gets
  one tool, `reexamine`, to explicitly look again before committing via a
  second tool, `submit_correction`, and decides for itself how many passes it
  needs — bounded at `MAX_TOOL_TURNS` internal turns, and at
  `max_correction_rounds=1` at the orchestrator level, so "decides for itself
  when to stop" never becomes "loops forever."

Verified end-to-end: a deliberately corrupted invoice (wrong total) was fed
through the real pipeline; the Correction Worker called `submit_correction`
on its first turn with the right values, re-validation came back clean, and
`retried_fields` correctly tracked which fields had been touched (used for
confidence scoring). A documented fallback exists if tool-calling doesn't
converge: one deterministic single-shot re-extraction of the retry group —
a real tradeoff, not a silent downgrade, per `spec/design.md` D6.

One real bug found and fixed during this: the model sometimes returns
corrected numeric values as currency strings (`"$606.34"`) inside the
free-form tool-call arguments (unlike the main extraction call, tool
arguments aren't constrained by `response_mime_type=application/json`).
Originally this caused Pydantic validation to fail silently and the
correction to be discarded. Fixed by coercing string values back to `float`
for fields typed as such, based on the field's declared annotation, before
merging — and by surfacing (not swallowing) the failure reason if
merge/validation still fails.

## Grounding: citation-level, not pixel-level

Full grounding — cropping the exact region a value came from — needs a
layout/bounding-box model, real infrastructure with no guarantee of working
on arbitrary invoice layouts inside a weekend. What ships instead: (1)
page-level grounding — the full source image next to the output in the UI —
and (2) `source_note`, a short text description the model gives of *where*
it read a value (e.g. "summary section Subtotal row"). This is the
deliberate, documented citation-level version of the pattern; bounding-box
grounding is scoped to Future Work, not silently claimed as built.

## User interface

Streamlit, styled with its native badge/status/container primitives (no
custom CSS injection needed — see `st.badge`, `st.status`, bordered
`st.container`). Beyond the image-next-to-report layout (D10):

- **Pipeline stages** — a visible tracker (Extract → Validate → Correction →
  Score confidence → Report) built directly from `orchestrator.py`'s own
  `PipelineResult.history`, so the architecture is visible, not just the
  final answer. Correction shows as skipped when validation passed on the
  first try, fired (green) when it didn't.
- **Agentic Correction panel** — added specifically because review surfaced
  a real gap: `retried_fields`, `correction_note`, and
  `correction_used_fallback` were already in pipeline state but `app.py`
  never displayed any of them, making the one agentic component in this
  project invisible from the UI. Now shown explicitly: which fields were
  re-examined, the model's own one-line rationale, and whether it resolved
  via real tool-calling or the deterministic fallback.
- **Document-type selector** — Invoice / Receipt, passing `schema_id`
  straight into `run_pipeline`, exercising the same schema-driven UI code
  for both.
- A sample-invoice picker for quick testing without a manual upload each time.

## Evaluation

`src/eval.py` runs the full pipeline (extraction → validation → confidence →
correction if needed → report) over the test set and compares against
hand-verified ground truth, now generalized (D17) to run over **both**
registered schemas and derive which fields to compare from each
ground-truth file's own keys — no hardcoded per-schema field list.

Test set: **17 hand-verified documents** (14 invoices, 3 receipts) — up from
the original 5, now spanning 3 distinct visual templates, 4 currencies
(USD/EUR/GBP/INR), varied optional-field combinations (tax-only,
discount+shipping, all four, none), multi-item and single-item invoices, one
deliberate date-order-warning case (due date before invoice date), and 3
deliberately blurred/rotated/noisy images — the first time this project's
test set has actually exercised the `ambiguous`/`unreadable` `field_status`
path rather than only clean digital PDFs. Generated via
`tests/generate_sample_invoices.py` (reportlab), each one hand-verified by
reading the actual rendered output — which caught a real bug: the ₹ (Rupee)
glyph rendered as a garbled tofu-box character, since reportlab's base-14
PDF fonts use WinAnsiEncoding, which covers $/€/£ but not that Unicode code
point. Fixed by using "Rs." instead of relying on the symbol glyph.

```
_TODO: real numbers pending — the Gemini free-tier daily quota (20
requests/day) was exhausted by this session's live testing. eval.py itself,
its scoring logic, and its file-discovery/ground-truth-matching are all
verified correct via unit tests and a dry run (see tests/unit/test_eval.py) —
what's still needed is one live run once the quota resets._
```

**Honest caveat, not a boast, even once real numbers land:** every document
in this set is either a digitally-generated PDF or a deliberately-degraded
image built from one — not an actual scan of a real invoice from a real
business. A high score here says the pipeline is *correct* on the range of
formats/currencies/degradations tested, not that it's robust to arbitrary
real-world scans (different fonts, handwriting, physical damage, unusual
layouts entirely outside these 3 templates).

## Limitations

- Test set (17 documents) is synthetically constructed, not sourced from
  real businesses — see the caveat above. Real-world invoice diversity
  (arbitrary templates, handwriting, physical scan artifacts) goes well
  beyond 3 templates and mild PIL-based degradation.
- No bounding-box grounding — citation-level text notes only (see above).
- Single invoice per file; no multi-invoice-per-file support.
- No jurisdiction-specific rules (GST/VAT) beyond generic tax-as-a-field
  handling — no rule actually validates a VAT number or rate.
- No precision/recall for line-item detection specifically (only field
  accuracy and extraction success rate, per the original eval scope).
- The Correction Worker's fallback path (deterministic single-shot retry, for
  when tool-calling doesn't converge) is implemented and unit-tested but not
  yet observed firing against a real document in this larger set — every
  live test so far resolved via real tool-calling on the first attempt.

## Future work

- Bounding-box highlighting (click a field, see the region on the invoice).
- Multi-invoice-per-file support.
- Jurisdiction-specific rules (GST, VAT) with real validation logic (VAT
  number format/checksum, rate-by-jurisdiction), not just tax-as-a-field.
- Precision/recall for line-item detection specifically.
- Real-world test invoices (actual scans, not constructed documents) to
  stress-test beyond what 3 authored templates can cover.

## Tech stack

Python 3.11, Pydantic v2, `pdf2image` + Poppler, Pillow, `google-genai`
(Gemini, free under Google Student Pro), Streamlit, pandas. Dev/test-only:
Ruff (lint + format), pytest, `reportlab` (test-data generation only — not a
pipeline dependency).

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

Regenerate the synthetic test set: `venv/Scripts/pip install -r requirements-test.txt && venv/Scripts/python tests/generate_sample_invoices.py`

## Screenshots

_TODO: add screenshots of the UI (source image + validation report side by
side) before submitting._

## Demo

_TODO: add a 60–90s demo link (upload → extraction → validation report with
a flagged field visible)._
