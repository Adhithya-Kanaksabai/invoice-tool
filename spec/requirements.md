# Requirements — Invoice Intelligence Tool

## Problem

Manual invoice data entry in accounts-payable is slow and error-prone. A human
reads a scanned invoice and retypes the vendor, dates, line items, and totals
into a system by hand. This tool automates the extraction and surfaces only the
values that need human review, so a person confirms instead of transcribes.

## Philosophy

- Engineering quality over feature quantity. Every feature must survive
  technical interview questioning.
- No "AI magic" — the LLM is never blindly trusted. Structured validation is
  the core of the project, not a bolt-on.
- Deadline is Monday. Scope is trimmed to fit that; anything cut is listed
  explicitly below so it's a deliberate tradeoff, not a gap.

## Goal

Given a scanned invoice (PDF or image), produce structured, validated data
(vendor, invoice number, dates, line items, totals), catch and flag anything
missing, low-confidence, or arithmetically inconsistent, and export the result
with a clear validation report for human review.

## Non-goals (explicit — do not build these)

- No authentication, accounts, or multi-user support.
- No database or persistence layer.
- No cloud deployment / hosting. Runs locally.
- No microservices, no Kubernetes, no unnecessary agent frameworks.
- No support for multiple invoices inside a single file (one invoice per file).
- No React frontend — Streamlit only. This project demonstrates AI systems
  engineering, not frontend engineering.
- No bounding-box / visual highlighting in the MVP — see Future Work.
- No GST or other jurisdiction-specific format rules unless the test set
  actually contains invoices that need them.

## Functional requirements

FR1. Accept a single invoice as either a PDF or an image file (jpg, png, webp).
FR2. Normalize any input into a list of page images before extraction.
FR3. Extract fields into a defined schema: vendor name, customer name
     (bill-to party), invoice number, invoice date, due date (optional),
     currency, line items (description, quantity, unit price, amount),
     subtotal, discount (optional), shipping (optional), tax (optional),
     total. Discount/shipping/tax are optional because real invoices don't
     all carry every one of these — see D16 in design.md.
FR4. Enforce that extraction output conforms to the schema (typed, structured
     JSON via Pydantic).

FR5. **Validation, two separate layers — do not mix them:**
     - **Schema validation** (structural correctness): required fields
       present, correct types, dates parse, numbers parse.
     - **Business validation** (domain correctness): line items sum to
       subtotal (within tolerance), subtotal - discount + shipping + tax =
       total (within tolerance, absent fields treated as 0 — see D16),
       invoice date <= due date, duplicate invoice number check against the
       current batch.

FR6. **Confidence is heuristic, never LLM-self-reported.** Derive it from
     signals such as: whether the field needed a retry, whether it passed
     business validation, whether it's arithmetically consistent, whether it
     agrees across multiple mentions in the document. The LLM does not
     invent a confidence number.

FR7. **Retry failed fields (and their dependencies) only, not the whole
     invoice.** On validation failure, send back the affected field(s), the
     original image, and the failure reason, and ask the model to re-extract
     just those. For arithmetic flags, "affected fields" is a GROUP, not a
     single field — see D6 in design.md for why (a subtotal mismatch can be
     caused by the subtotal itself OR the line items it's supposed to sum,
     so both must be re-extracted together or the retry can't actually
     resolve the mismatch).
     Fallback (if this proves unreliable under time pressure): whole-invoice
     retry with the failure reason injected — documented as a time-boxed
     tradeoff, not silently swapped in.

FR7a. **Hard extraction failures (distinct from validation failures).** If
      the vision LLM call itself fails — API error, timeout, or a response
      that isn't parseable at all — this is NOT a validation retry (FR7).
      Retry the raw call up to 2 times with backoff. If still failing, mark
      the invoice `extraction_failed` at the top level and move on (don't
      crash the batch/eval run over one bad invoice).

FR8. **Validation report.** One rendered view (in the Streamlit UI and the
     export) grouping results into PASS / Warnings / Errors per field. This
     is the same underlying flag data as FR5/FR6 — one artifact, not a
     separate report-building step.

FR9. Export the final result as JSON and CSV, with the validation report
     grouping preserved in both.

FR10. **UI shows the source image next to the extracted output**, not just
      the JSON/report in isolation. This is required for a human to actually
      verify an extraction — a document tool that doesn't show the document
      next to its answer isn't reviewable.

FR11. **Each field carries an explicit status** — extracted, missing,
      ambiguous, or unreadable — rather than the model being forced to
      guess a value. The extraction prompt instructs the model to use these
      statuses. This is a separate signal from confidence (FR6) and business
      validation (FR5); the report keeps all three visible, not merged.

FR12. **Each field carries a citation-level source note** — a short text
      description of where on the page the value was read from (e.g. "table
      row 3"). This is the achievable version of grounding for this
      timeline: page-level image display (FR10) + a text citation, not
      pixel-level bounding boxes. See design.md D14.

## Evaluation

- Build a small ground-truth dataset (5-10 invoices, hand-verified correct
  values).
- Metrics: **field-level accuracy** (did we get the right value, per field)
  and **extraction success rate** (did the pipeline complete without
  crashing, per invoice). No precision/recall — those are classification
  metrics and don't map cleanly onto field extraction. (Exception: if line
  item *detection* — did we find the right number of line items, did we
  hallucinate extras — turns out to be a real failure mode in testing,
  precision/recall can be scoped specifically to that subproblem. Not
  required for MVP.)

## Acceptance criteria

- Running the tool on a sample invoice produces schema-valid JSON.
- Schema and business validation failures are distinguishable in the output.
- Arithmetic inconsistencies are caught and flagged, not silently passed.
- A missing required field produces a flag, not a crash.
- Confidence scores trace back to a stated heuristic, not a model guess.
- The eval script reports field accuracy and extraction success rate across
  the test set.

## Future work (explicitly deferred, not forgotten)

- Bounding-box highlighting (click a field, see the region on the invoice).
- Multi-invoice-per-file support.
- Jurisdiction-specific rules (GST, VAT) once real test data exists.
- Precision/recall for line-item detection specifically.

## Decided

- Confidence threshold for flagging: 0.7 (`confidence.py::CONFIDENCE_THRESHOLD`).
- Arithmetic tolerance: $0.01 (`business_validate.py::AMOUNT_TOLERANCE`) —
  rounds to the nearest cent rather than requiring exact float equality.
- Currency: single currency assumed per invoice, no multi-currency detection
  (`schema.py::Invoice.currency`, defaults `"USD"`).
