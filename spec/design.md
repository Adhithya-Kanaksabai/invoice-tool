# Design — Invoice Intelligence Tool

## Orchestration philosophy (read this first)

This project is structured as one generic orchestrator running domain-specific
workers, with invoices as the first workflow on top of it. Two rules keep this
honest rather than over-engineered:

1. **We do not build for a second workflow that doesn't exist yet.** No
   SpecFlow-specific code, no speculative config options "in case" a future
   workflow needs them. Premature generalization — designing for a use case
   you haven't actually built — produces abstractions that don't fit what you
   eventually build anyway, and this project has a Monday deadline. If a
   second workflow gets built later, THAT is when shared logic gets extracted,
   with a real second data point instead of a guess.

2. **The reusable part is narrow and specific: the orchestrator/worker
   CONTRACT, not the workers themselves.** Every worker takes an opaque state
   dict and returns a `WorkerResult` (status + new state + optional reason).
   The orchestrator only ever reads `status` and the step list — it never
   looks inside `state`. That's the entire reusable surface. The workers
   themselves (extract, validate, retry, report) stay honestly
   invoice-specific internally — an invoice Validation Worker and a future
   SpecFlow Validation Worker will never share logic, only the shape of the
   handoff is shared.

This also happens to be exactly the seam that makes a later move to LangGraph
straightforward without having paid for LangGraph now: LangGraph nodes are
also "take state, return state" — the contract ports directly, plain Python is
enough today.

## Orchestrator / worker contract

    @dataclass
    class WorkerResult:
        status: str        # "ok" | "retry" | "failed"
        state: dict         # opaque to the orchestrator — worker-defined shape
        reason: str | None = None

    def run_pipeline(image, workers: list[Worker]) -> WorkerResult:
        state = {"image": image}
        for worker in workers:
            result = worker(state)
            state = result.state
            if result.status == "retry":
                state = correction_worker(state, result.reason)
            if result.status == "failed":
                return result
        return result

`orchestrator.py` is the ONLY file that knows the step order. It never imports
`Invoice`, never knows what "subtotal" means, never contains a business rule.
Everything domain-specific lives inside a worker, behind the `WorkerResult`
boundary.

## Workers (the invoice workflow, as steps on the generic orchestrator)

    Orchestrator
          |
          v
    Extraction Worker      (extract.py)      -> vision LLM call
          |
          v
    Validation Worker       (schema_validate.py + business_validate.py)
          |
          v
    Correction Worker       (retry.py)  <-- the one agentic loop, see below
          |
          v
    Validation Worker        (re-run, same worker as above)
          |
          v
    Report Worker           (report.py)
          |
          v
    Export                 (export.py — not a worker, just I/O)

This is the same 5-stage pipeline this project has had since the first draft
of design.md — nothing about the actual invoice logic changed. What changed is
that the orchestrator now talks to workers through one generic contract
instead of importing `Invoice` and calling functions directly. That's a
~30-45 minute refactor of the existing modules, not a rewrite.

## The one agentic loop (and why nowhere else)

Every step was evaluated for whether real agentic behavior (the LLM deciding
what to do next, via tool-calling, rather than code deciding) would genuinely
help, versus just adding buzzword surface:

- **Extraction** — one deterministic call, image in, schema out. No decision
  to make. Not agentic.
- **Validation** — pure rule-checking (arithmetic, required fields).
  Deliberately deterministic per D3/D4 — an LLM should never be the one
  deciding whether `subtotal + tax == total`. Must stay non-agentic.
- **Report / Export** — formatting only. Nothing to decide.
- **Correction (retry) — YES, this is the one.** "Given this specific
  validation failure, figure out what to re-examine and how" is a genuine
  judgment call, not a fixed rule — unlike validation, there's no single
  correct procedure to hardcode. This is the one place giving the model a
  tool (e.g. `re_read_region(field_group, reason)`) and letting it decide when
  it's satisfied, rather than code deciding in advance, does real work instead
  of decorating a deterministic system.

Everything else in this project stays deterministic. One loop, chosen because
it earns its place, not because agents are trendy.

## Borrowed ideas (from Document AI course material) — what's in, what's out, and why

A document-intelligence course (DeepLearning.AI x LandingAI, covering OCR
evolution, layout/ADE, grounded RAG, and cloud production architecture) was
reviewed for architectural ideas applicable to this project. Most of the
course targets problems this project doesn't have — RAG, multi-tenant cloud
ingestion, chunk persistence at scale. Three ideas earned their way in; the
rest were deliberately left out, listed here so the decision is a record, not
a gap.

**In:**

- **Explicit per-field status (D13).** Instead of every field resolving to a
  value or a pass/fail flag, each field gets `extracted | missing | ambiguous
  | unreadable`. The model is instructed to use these rather than guess when
  uncertain. This is a genuinely different failure mode from "value present
  but arithmetically wrong" (business validation) — a missing field and a
  wrong field need different handling, and conflating them was a real gap in
  the original design. See `schema.py::FieldStatus`.

- **Three separate signals, not one (already partially in place, now named
  explicitly).** The course's framing: OCR confidence, extraction confidence,
  and business-rule validation are different signals and none alone proves
  correctness. This project doesn't have separate OCR confidence (Gemini does
  OCR+extraction in one call, per D1), but it already has two of the three:
  heuristic confidence (`confidence.py`) and business validation
  (`business_validate.py`). The change here is presentational, not
  architectural: the Report Worker keeps these visibly separate rather than
  collapsing them into one flag list, so a reviewer can tell "the model was
  unsure" from "the math doesn't add up."

- **Citation-level grounding (D14) — the cheap version, not the real one.**
  The course's grounding is pixel-level: crop the exact bounding box a value
  came from and show it. That requires a layout/bbox model and reliable
  region detection — real infrastructure, not guaranteed to work well in a
  weekend build on arbitrary invoice layouts, and not something to silently
  claim if it isn't built. What ships instead: (1) page-level grounding,
  already planned as D10 — show the full source image next to the output —
  and (2) `source_note`, a short text description the model provides of
  *where* on the page it read a value (e.g. "table row 3"), no coordinates.
  This is explicitly the citation-level version of the pattern, documented as
  a simplification, with pixel-level bounding boxes named in Future Work.

**Out, and why:**

- **Typed chunk decomposition** (splitting a page into text/table/figure
  regions before extraction, routing each to specialized tools). This solves
  layout ambiguity on complex, multi-column, mixed-content pages. Most
  invoices are single-column and already suited to one full-page vision call
  (D1) — adding a decomposition stage would solve a problem this document
  type doesn't exhibit. If testing on real invoices reveals genuinely complex
  layouts breaking extraction, this is the first thing to reconsider — but
  don't build it preemptively.

- **Chunk/artifact persistence with document_id/chunk_id/parser_version
  metadata.** This is the schema for a system that stores many documents over
  time and needs audit trails. This project has no database and no
  persistence layer by design (non-goals, requirements.md) — adopting a
  persistence-oriented schema with nothing to persist to is cargo-culting a
  contract this project can't use.

- **RAG, embeddings, retrieval.** Out of scope entirely — this project
  extracts structured fields from one invoice at a time, it doesn't answer
  open-ended questions across a document corpus. Different problem.

- **Agent tool permissioning/idempotency/observability infrastructure.**
  Real concerns for a production, multi-tenant, cloud-deployed system. This
  project explicitly has no cloud deployment (non-goals). The one agentic
  loop it does have (Correction Worker, D11) is already bounded by a
  code-enforced cap — that's the right-sized version of "bounded" for this
  project's scale, not the full production pattern.

## Architecture (invoice workflow detail)

    Invoice (PDF/image)
          |
          v
    Ingest & normalize -> list of page images
          |
          v
    Vision LLM extraction -> raw structured output
          |
          v
    Schema validation (Pydantic) -- structural correctness
          |
          v
    Business rule validation -- domain correctness
          |
          v
    Confidence scoring (heuristic, derived from the above)
          |
    +-----+------------------------------+
    |                                    |
    | any field failed business rules?   |
    |                                    |
    v yes                                v no
    Retry failed fields only             (skip)
    (re-prompt with failure reason,      |
    re-validate just those fields)       |
    |                                    |
    +-----------------+------------------+
                       |
                       v
              Validation report
              (PASS / Warnings / Errors)
                       |
                       v
                 Export (JSON + CSV)

## Key decisions (and why)

D1. **Vision LLM for OCR + structuring, no separate OCR-then-parse step.**
    Invoices are tabular and inconsistently formatted. A vision model reads
    layout in one pass; a flat-text OCR engine would need a second structuring
    step. Cost/latency are negligible at this scale.

D2. **Normalize all input to page images early.**
    PDFs render to PNGs, images are validated/resized. Everything downstream
    is input-agnostic.

D3. **The schema is the contract, and validation is layered, not merged.**
    - Schema validation = "is this well-formed" (types, required fields,
      parseable dates/numbers). Enforced by Pydantic directly.
    - Business validation = "is this correct given domain rules" (arithmetic,
      date ordering, duplicate detection). A separate function, separate from
      Pydantic's own validation, because these are different failure classes
      with different retry implications. Mixing them makes it impossible to
      answer "was this a malformed value or a domain-inconsistent one?" —
      which is exactly the question an interviewer will ask.

D4. **Validation is soft, not fatal.** Failures produce flags, not exceptions.
    The system's job is to surface issues for a human, not to block.

D5. **Confidence is derived, not requested.** LLMs self-reporting a confidence
    number is not calibrated to anything real. Instead, confidence per field
    is computed from actual signals:
    - required retry -> lower confidence
    - failed business validation -> lower confidence
    - arithmetically consistent with the rest of the document -> higher
    - appears consistently across multiple mentions (e.g. total shown twice)
      -> higher
    This keeps confidence honest and explainable — you can point at exactly
    why a field is flagged, not just cite a model's guess.

D6. **Retry targets a dependency GROUP, not always a single field.**
    A naive version of "retry only the failed field" breaks on arithmetic
    flags. If `line_sum != subtotal`, the flag names `subtotal` — but the
    real error could be in `subtotal`, in one of the `line_items`, or both.
    Retrying `subtotal` alone does nothing if the line items were the actual
    mistake: the model re-reads the same (correct) subtotal, re-validates
    against the same (wrong) line items, and the mismatch persists —
    potentially looping.

    So arithmetic flags map to a RETRY GROUP of fields that must be
    re-extracted together:
    - subtotal mismatch -> re-extract {line_items, subtotal} together
    - total mismatch -> re-extract {subtotal, tax, total} together

    Schema-layer flags (missing/empty field) stay single-field — a missing
    vendor name has no dependency on anything else, so there's no group to
    consider.

    The retry prompt includes the original image, the full group of fields
    being re-extracted, and the specific failure reason (e.g. "line items
    sum to 245.00 but subtotal reads 240.00 — re-check both against the
    image").

    Time-box: if field-level retry proves unreliable (model doesn't respect
    the narrowed scope, or the loop doesn't converge) with less than a day
    left, fall back to whole-invoice retry with the reason injected, and
    document this as a deliberate tradeoff in the README — not a silent
    downgrade. Also cap retries at 1 attempt regardless — if the group retry
    still doesn't resolve the mismatch, flag it as unresolved for human
    review rather than retrying indefinitely.

D7. **The validation report is a rendering of existing data, not a new
    artifact.** Schema flags + business flags + confidence scores already
    contain everything needed. The "report" is a PASS/Warning/Error grouping
    view over that same flag list, shown in Streamlit and included in the
    export. No separate report-generation logic.

D9. **Hard extraction failures are handled separately from validation
    failures.** FR7/D6 cover the case where extraction succeeds but the
    values are wrong. A different failure mode: the vision LLM call itself
    fails (API error, timeout, unparseable response). This is infrastructure
    failure, not correction — handled in `extract.py` with its own small
    retry-with-backoff (2 attempts), separate from the field-group retry in
    `retry.py`. If still failing, the invoice is marked `extraction_failed`
    at the top level rather than raising — this matters most for `eval.py`,
    where one bad invoice must not crash the whole batch and silently zero
    out your accuracy numbers for the rest of the set.

D8. **Streamlit, not React.** This project demonstrates AI systems
    engineering — the pipeline, the validation, the retry logic — not
    frontend engineering. Streamlit gets a usable UI up with minimal time
    spent on it, which is the correct tradeoff here.

D10. **The UI always shows the source image next to the output.** Without
     this, neither you nor a future user can actually verify an extraction —
     you'd have to open the file separately. `st.columns([1,1])`: image on
     the left, extracted fields + validation report on the right. Small
     effort (~20 min), required for the tool to be reviewable at all, not
     an optional polish item.

D11. **The Correction Worker is the one agentic component; everything else
     is deterministic by design, not by accident.** Concretely: the
     Correction Worker gets a tool like `re_read_region(field_group, reason)`
     and decides, via the model's own tool-calling, when it has enough
     information to stop — rather than the orchestrator hardcoding "call
     once, check, done." Bounded with a hard cap (max 1 correction round,
     matching D6's retry cap) so an agentic loop can't run away — an agent
     that decides for itself when to stop still needs a code-enforced
     ceiling, or "decides for itself" becomes "loops forever." If this proves
     unreliable under time pressure, the fallback is the deterministic
     single-shot retry from D6 (still real, still correct — a legitimate
     tradeoff to describe honestly in the README) not a silent downgrade.

D12. **The orchestrator/worker contract is the only "reusable engine"
     surface, and it is intentionally thin.** `WorkerResult{status, state,
     reason}` is the entire interface. No shared config system, no plugin
     registry, no generic "workflow definition" DSL — those would be
     speculative generalization for workflows that don't exist yet (see
     Orchestration Philosophy above). If a second workflow gets built later,
     extract further shared structure then, from two real examples instead
     of one guess.

D13. **Per-field status is explicit, separate from confidence and from
     business validation.** `FieldStatus` (extracted/missing/ambiguous/
     unreadable) is a THIRD signal alongside heuristic confidence
     (`confidence.py`) and business validation (`business_validate.py`) —
     borrowed from the course's "OCR confidence, extraction confidence, and
     business-rule validation are different signals, none alone proves
     correctness" principle. The extraction prompt instructs the model to
     use these statuses rather than force a guess. The Report Worker keeps
     all three visible separately, not collapsed into one flag list.

D14. **Grounding is citation-level (text description of source location),
     not pixel-level (bounding boxes).** Full grounding — cropping the exact
     region a value came from — needs a layout/bbox model and is real
     infrastructure work with no guarantee of reliability on arbitrary
     invoice layouts within a weekend. What ships: page-level grounding
     (D10, the full image next to the output) plus `source_note` — a short
     text description the model gives of where it read a value (e.g. "table
     row 3", "top-right header block"). This is a deliberate, documented
     simplification of the grounding pattern, not a silent claim to have
     built pixel-level grounding. Bounding-box grounding stays in Future
     Work alongside bounding-box UI highlighting (already scoped there).

D15. **A schema registry (`schema_registry.py`) is the one place "which
     document type" is looked up — not a general schema DSL.** Before this,
     `Invoice` was imported by name in `schema_validate.py`,
     `business_validate.py`, and `confidence.py` — adding a second document
     type would mean editing all three. Now: `schema_validate.py` takes a
     `schema_id` string and looks up the Pydantic model, required fields,
     business rules, and retry groups from `REGISTRY`. Layer 1 validation
     (`schema_validate.py`) is now fully schema-agnostic — it never imports
     `Invoice`. Layer 2 (`business_validate.py`) stays honestly
     invoice-specific internally (rules are plain Python functions, e.g.
     `check_line_items_sum`), but is packaged as `INVOICE_BUSINESS_RULES: list[Callable]`
     so the registry can hold a different rule list per schema without
     sharing logic across document types — invoice rules and a future
     receipt's rules will never call into each other, only the *shape*
     (`Callable[..., list[Flag]]`) is shared.

     Explicitly NOT built, and why: a generic field-type wrapper over
     Pydantic (Pydantic already is the generic schema layer — wrapping it
     adds indirection with no payoff); a business-rule config DSL (a rule
     syntax generic enough for both "line items sum to subtotal" and
     "PO number matches a contract" would itself be a multi-week project,
     and would produce something worse than a Python function); a generic
     prompt-builder for arbitrary schemas (prompt quality is schema-specific
     — what makes a good invoice extraction prompt differs from what makes a
     good contract prompt, and there's nothing to generalize from a sample
     size of one). To add a second document type later: write its Pydantic
     model, its business rule functions in the same shape, its retry groups,
     register one `DocumentSchema` entry. Nothing in `orchestrator.py` or
     `schema_validate.py`'s generic path changes.

D16. **The schema and arithmetic rule were generalized after checking the real
     test set.** T2 (tasks.md) says "review schema.py against your actual
     sample invoices" — doing that surfaced a real mismatch: all 5 sample
     invoices (a SuperStore-generated template) never show a `tax` line at
     all, show `shipping` on every invoice, and show `discount` on some but
     not others. None show a due date. The original schema assumed
     `subtotal + tax = total` and a required `tax` field — neither holds for
     this test set, which would have made `check_total_arithmetic` flag every
     single invoice as a business-rule error regardless of correctness,
     silently invalidating the eval numbers this project's tasks.md calls
     "the strongest resume signal."

     Fix: `discount`, `shipping`, and `tax` are now all `Optional[float] = None`
     on `Invoice` (absent means 0, not "extraction failed"), and
     `check_total_arithmetic` checks the generalized formula
     `subtotal - discount + shipping + tax == total`, which correctly reduces
     to the original `subtotal + tax = total` when discount/shipping are
     absent — this is a strict generalization, not a special case for one
     invoice template. Also added `customer_name` (the "Bill To" party) as a
     required field alongside `vendor_name` — in this test set `vendor_name`
     is constant ("SuperStore") so `customer_name` is the field that actually
     varies per invoice and is worth validating.

     `total`'s RETRY_GROUP was widened from `{subtotal, tax, total}` to
     `{subtotal, discount, shipping, tax, total}` to match — per D6, a retry
     group must cover every field the arithmetic check depends on, or a
     mismatch caused by a wrong `discount`/`shipping` read would never
     resolve.

D17. **A second document type (`receipt-v1`) was actually built, to prove
     D12/D15's reusability claim instead of leaving it asserted.** `Receipt`
     is a deliberately different shape than `Invoice` — `merchant_name`
     instead of `vendor_name`/`customer_name`, `transaction_date` instead of
     `invoice_date`/`due_date`, `tip` and `payment_method` which `Invoice`
     doesn't have at all — with its own independent business rules in
     `business_validate_receipt.py` (`check_receipt_items_sum`,
     `check_receipt_total_arithmetic`, `check_receipt_duplicate_transaction_id`),
     never calling into `business_validate.py`'s functions, per D15's own
     rule that only the *shape* (`Callable[..., list[Flag]]`) is shared
     across schemas.

     Registering it required zero changes to `orchestrator.py` or
     `schema_validate.py`'s generic path — confirming the claim. It DID
     surface three real genericity bugs that a single-schema registry had
     been hiding:

     - `retry.py` imported `business_validate.retry_field_groups` directly
       by name instead of reading `doc_schema.retry_groups` from the
       registry — would have silently applied invoice retry groups to a
       receipt's field names. Fixed by expanding retry groups generically
       inside `retry.py` itself, driven by whichever schema's
       `retry_groups` dict the registry returns.
     - `validate.py` passed a hardcoded `seen_invoice_numbers` kwarg to
       every business rule. A receipt's duplicate-check rule expects
       `seen_ids` (its natural id is `transaction_id`, not
       `invoice_number`) — the mismatched kwarg name would have been
       silently absorbed by the rule's `**_` catch-all, so the receipt's
       duplicate-transaction check would never have fired, with no error
       raised anywhere. Fixed by making the kwarg name itself
       schema-agnostic (`seen_ids`) — every schema's own rule function
       decides which of its fields that maps to.
     - `report.py` and `confidence.py` both excluded the field named
       `"line_items"` specifically to find the one list-typed field on the
       model. Receipt's list field is `"items"`. Fixed by detecting the
       list-typed field generically, by Pydantic annotation
       (`typing.get_origin(...) is list`), not by name — added
       `schema_registry.get_scalar_field_names()` /
       `get_list_field_name()` as the one shared place this detection
       happens, used by `report.py`, `confidence.py`, `app.py`, and
       `eval.py` instead of each reimplementing it.

     Also renamed the pipeline state key from `state["invoice"]` to the
     generic `state["document"]` across every worker (`extract.py`,
     `validate.py`, `retry.py`, `report.py`, `export.py`, `app.py`,
     `eval.py`) — the orchestrator/worker contract was already schema-
     agnostic in principle, but the state key itself said "invoice"
     everywhere, which was never actually tested against a non-invoice
     schema until now.

     All three bugs were only findable by actually registering and running
     a second schema — exactly why this was worth building now rather than
     leaving the claim as an assertion in Future Work.

## Tech stack

- Python 3.11+
- `pydantic` — schema definition and structural validation
- `pdf2image` (+ poppler) — PDF page rendering
- `Pillow` — image resize/encode
- `google-genai` — vision LLM calls (Gemini). Chosen over Claude/GPT-4o for
  this project specifically because it's free under the Google Student Pro
  plan — meaningful when iterating on extraction prompts dozens of times over
  a weekend. Architecture is provider-agnostic; swapping providers later is a
  change to `extract.py` only, not to schema/validation/retry logic.
- `pandas` — CSV export / report shaping, if needed
- `streamlit` — UI
- Dev/test-only (not pipeline dependencies): `ruff` (lint + format), `pytest`,
  `reportlab` (synthetic test-invoice generation, `tests/generate_sample_invoices.py`)

## Modules

- `orchestrator.py`   — the ONLY file that knows step order. Runs the worker
                       list, reads `WorkerResult.status`, decides
                       continue/retry/stop. Never imports `Invoice` or
                       anything invoice-specific.
- `schema_registry.py` — looks up Pydantic model + business rules + retry
                       groups + required fields by `schema_id` (D15). Two
                       schemas registered as of D17: `invoice-v1`,
                       `receipt-v1`. Also the one shared place that detects
                       a schema's scalar vs. list-typed fields generically
                       (`get_scalar_field_names`, `get_list_field_name`),
                       by Pydantic annotation, not by field name.
- `ingest.py`      — load file, detect type, render/resize to page images, base64 encode
- `extract.py`      — Extraction Worker. Build the extraction prompt with the schema,
                       call the vision LLM (retry-with-backoff x2 on hard API/parse
                       failures per D9), parse to Pydantic, return WorkerResult
- `schema_validate.py`  — part of the Validation Worker: schema-agnostic structural
                       validation, takes a `schema_id` and looks up the model via
                       schema_registry.py (types, required fields, parsing)
- `business_validate.py` — part of the Validation Worker: invoice-specific domain
                       rules (arithmetic, date ordering, duplicate invoice numbers),
                       packaged as `INVOICE_BUSINESS_RULES` for schema_registry.py.
                       Also defines RETRY_GROUPS mapping arithmetic flags to their
                       field dependency group (per D6).
- `business_validate_receipt.py` — the receipt-v1 counterpart (D17), independent
                       of business_validate.py per D15 — its own arithmetic/
                       duplicate-check rules and its own RECEIPT_RETRY_GROUPS.
- `confidence.py`   — heuristic confidence scoring from validation + retry signals
- `retry.py`        — Correction Worker (the one agentic loop, per D11). Gives
                       the model a tool to re-examine a RETRY_GROUP and decides
                       via tool-calling when it's done, capped at 1 round.
                       Expands retry groups generically via doc_schema.retry_groups
                       (D17) rather than importing business_validate.py by name.
- `report.py`        — Report Worker: shape flags + confidence into the
                       PASS/Warning/Error grouping
- `export.py`        — write JSON + CSV, report grouping included (plain I/O,
                       not a worker — nothing to orchestrate)
- `app.py`          — Streamlit UI: image + validation report side by side (per D10),
                       a pipeline-stage tracker and an Agentic Correction panel driven
                       off orchestrator.py's own PipelineResult.history/state, and a
                       document-type selector (Invoice/Receipt, per D17)
- `eval.py`          — run the pipeline over both registered schemas' test sets,
                       compare to ground truth, report field accuracy + extraction
                       success rate (extraction_failed invoices count against success
                       rate, excluded from field accuracy since there's no output to
                       score). Generalized (D17) to derive comparison fields from
                       each ground-truth file's own keys, not a hardcoded field list.
- `tests/generate_sample_invoices.py` — test-data authoring script (reportlab),
                       not part of the production pipeline; generates the diverse
                       17-document test set, hand-verified afterward by reading the
                       actual rendered output.

## Data flow contract

Every worker returns `WorkerResult{status, state, reason}`. `state` is opaque
to the orchestrator; within a document workflow (invoice or receipt) it carries:

- after Extraction Worker: `state["document"]` = the raw, schema-validated
  document instance (unvalidated in the business-rule sense — `Invoice` or
  `Receipt`, whichever `schema_id` resolved to)
- after Validation Worker: `state["document"]`, `state["flags"]` = schema flags + business flags
- after Correction Worker (if triggered): `state["document"]` updated for the
  retried field group, `state["retried_fields"]`, `state["correction_note"]`,
  `state["correction_used_fallback"]` — the latter two added so the UI can
  actually display what the Correction Worker did (see app.py above)
- confidence scoring reads `state["flags"]` + `state["retried_fields"]`,
  writes `state["confidence"]`
- after Report Worker: `state["report"]` = grouped `{pass: [...], warnings: [...], errors: [...]}`

(Prior to D17 this was named `state["invoice"]` — renamed to the generic
`state["document"]` across every worker, since the key itself hardcoded
"invoice" even though the contract was already meant to be schema-agnostic.)
- `export.py` reads final `state` and writes `result.json`, `result.csv`

## Decided (previously open)

- Confidence threshold for flagging as low-confidence: 0.7.
- Arithmetic tolerance: rounded to nearest cent ($0.01), not exact match.
- Currency: assume single currency per invoice, no detection.
