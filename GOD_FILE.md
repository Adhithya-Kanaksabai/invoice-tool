# GOD_FILE — Invoice Intelligence Tool, interview prep

This is the "what I'd actually say out loud" version of this project. `README.md` is the
engineering reference; `spec/design.md` has the full decision log (D1–D17). This file is for
talking through the project in an interview, not for documenting the code.

## Elevator pitch

It's a tool that takes a scanned invoice or receipt — a PDF or an image — and turns it into
structured, validated data: vendor, customer, dates, line items, totals. Instead of trusting
whatever a vision LLM says, it runs the extraction through two layers of validation, catches
arithmetic and structural problems automatically, and only asks a human to look at the specific
fields that are actually in question — not re-check the whole document by hand. It handles two
different document types (invoices and receipts) on the same underlying engine, which is the
actual proof that the architecture generalizes, not just a claim about it.

## Problem it solves

In accounts-payable, someone reads a scanned invoice and retypes it into a system by hand —
vendor, dates, every line item, totals. It's slow and error-prone, and the retyping itself adds
no value; the value is in catching mistakes. This tool automates the transcription and turns the
human's job into "confirm these three flagged fields," not "retype the whole invoice."

## Architecture, in plain English

The pipeline is one **generic orchestrator** running a list of **workers**, where invoices were
the first workflow built on top of it and receipts are the second. Every worker is a plain
function: it takes the current state (a dict) and returns a `WorkerResult` — a status (`ok`,
`retry`, or `failed`), the updated state, and an optional reason. The orchestrator only ever looks
at that status. It never imports the `Invoice` or `Receipt` type, never knows what a subtotal is,
never contains a business rule.

Why bother with that split instead of just writing one pipeline function? Because it means the
*only* thing that's "generic" and reusable is the handoff shape between steps — not the workers
themselves. Each document type gets its own extraction/validation/retry workers where it matters;
only the shape of `WorkerResult` is shared, nothing document-specific leaks into the orchestrator.
That's a deliberately narrow "reusable engine" — no speculative plugin system or config DSL for
workflows that don't exist yet.

The actual pipeline, in order: ingest the file into page images → extract with a vision LLM
(Gemini) → validate structurally (schema) → validate against business rules (arithmetic, dates,
duplicates) → if something failed, hand off to a Correction Worker that re-examines just the
affected fields → re-validate → build a PASS/Warnings/Errors report → export as JSON/CSV, and
show it all in a Streamlit UI with the source image next to the output, a live pipeline-stage
tracker, and an explicit panel showing whether/how the agentic Correction Worker fired.

## Key decisions and the reasoning

- **Validation is two separate layers, not one function.** Schema validation asks "is this
  well-formed" (right types, required fields present). Business validation asks "is this
  correct" (does the math add up, is the invoice date before the due date). Keeping them
  separate means I can always answer "was this a malformed value or a domain-inconsistent one" —
  which matters because they need different fixes.

- **Confidence is computed, never asked from the model.** An LLM stating its own confidence
  isn't calibrated to anything real. Instead, confidence per field comes from actual signals:
  did it need a retry, did it pass business validation. It's deliberately simple — high if clean,
  lower if retried, low if still flagged — because a simple heuristic I can fully explain beats a
  fancier one I can't.

- **Only one part of this is agentic, on purpose.** Extraction is one deterministic call.
  Validation is pure rule-checking — an LLM should never get to decide whether `subtotal + tax
  == total`. The Correction Worker is the one place where "figure out what to re-examine and
  how" is a genuine judgment call without one correct fixed procedure — so that's the one part
  where the model gets a tool and decides for itself when it's done, bounded by a hard cap so
  "decides for itself" doesn't become "loops forever." I deliberately did NOT add more agentic
  components just to pad this out for a resume — a second document type (see below) was the
  actual way to demonstrate more depth, not fake multi-agent complexity around arithmetic checks
  that a plain Python function already handles correctly and explainably.

- **A registry, not hardcoded document knowledge.** A `schema_registry` maps a schema ID
  (`"invoice-v1"`, `"receipt-v1"`) to its model, business rules, and retry groups. Before this,
  `Invoice` was imported by name in three different files. Now the validation and confidence code
  never imports a specific document type directly — they take a schema ID and look everything up.

- **Citation, not bounding boxes.** Real grounding — cropping the exact region a value came from
  — needs a layout model, which is real infrastructure with no guarantee of working reliably on
  arbitrary invoices in a weekend. What ships instead: the full source image next to the output,
  plus a short text note from the model on where it read each value (e.g. "summary section,
  Subtotal row"). That's a deliberate, named simplification, not a silent shortcut.

## The second document type — proving reusability instead of asserting it

The single biggest architectural addition after the first version: registering `receipt-v1`
alongside `invoice-v1` on the exact same orchestrator and registry, deliberately with a different
shape (`Receipt` has a merchant and a transaction, not a vendor/customer billing relationship —
no `due_date`, but it does have `tip` and `payment_method`, which `Invoice` doesn't). This
required **zero changes** to the orchestrator or the schema-agnostic structural validator — which
is the actual test of whether "reusable engine" was true, not just a claim in a README.

It also surfaced three real bugs that a single-schema system had been hiding the whole time:

1. The Correction Worker imported the invoice's retry-group logic *by name* instead of asking the
   registry for whichever schema's retry groups actually applied — would have silently used
   invoice retry groups on a receipt's fields.
2. The validator passed a hardcoded `seen_invoice_numbers` argument to every business rule. The
   receipt's duplicate-check rule expects a differently-named argument (its natural id is a
   transaction ID, not an invoice number) — the mismatched name would've been silently swallowed
   by the rule's catch-all, so duplicate-transaction detection would never have actually fired,
   with no error anywhere telling you that.
3. Two modules found "the list of items" on a document by checking for a field literally named
   `"line_items"` — which doesn't exist on a receipt (it's called `"items"` there). Fixed by
   detecting the list-typed field by its actual Python type, not by guessing a name.

None of these three bugs were visible with only one schema registered — they only exist because
nothing had ever exercised the "generic" path with a genuinely different second shape. That's the
whole argument for building this now instead of leaving it as an assertion.

## Problems encountered and how they got fixed

**The schema didn't match the real invoices.** The spec assumed every invoice has a `tax` field
and that `subtotal + tax = total`. The original 5 sample invoices turned out to be one generated
template that never shows tax at all — it shows `shipping` always and `discount` sometimes. Under
the original schema, the arithmetic check would have flagged *every single invoice* as a
business-rule error regardless of whether the extraction was right, which would have silently
zeroed out the eval numbers. Fix: made discount/shipping/tax all optional (absent = 0) and
generalized the formula to `subtotal - discount + shipping + tax = total`, which is a strict
generalization — it still reduces to the original formula when discount/shipping are absent, it
doesn't just special-case one invoice template. This came from actually opening the sample PDFs
and checking, not assuming the spec was right.

**A pinned model name went stale mid-build.** `extract.py` was first written against
`gemini-2.5-flash`, which turned out to be deprecated for new API keys — the very first real API
call 404'd. Fixed by switching to `gemini-flash-latest`, Google's stable alias, and by first
querying `client.models.list()` to check what was actually available instead of guessing a model
name from memory.

**The agentic Correction Worker's tool calls returned currency strings, not numbers.** The main
extraction call uses `response_mime_type="application/json"`, which keeps output types honest.
Tool-calling arguments aren't constrained the same way — the model correctly identified and fixed
a corrupted total on its first attempt, but returned the value as `"$606.34"` instead of `606.34`.
That silently failed Pydantic validation and the correction got discarded with no visible error.
Fixed two things: added coercion that strips currency formatting and converts to float for fields
actually typed as numeric, based on the field's own declared type; and stopped swallowing the
validation failure silently, so if it happens again it's visible instead of a silent no-op.

**Poppler wasn't on PATH after installing it mid-session.** `pdf2image` needs Poppler's binaries,
which aren't a pip package. Installed via winget, but Windows requires a shell restart to pick up
a PATH change, which wasn't an option mid-session. Fixed by having `ingest.py` fall back to the
known winget install location if `pdftoppm` isn't found on PATH, instead of just failing.

**The orchestrator silently dropped the actual failure reason.** While rebuilding the UI's error
path, `app.py` tried to display `result.reason` on a failed pipeline run and hit an
`AttributeError` — `PipelineResult` never actually carried a `reason` field at all; the
orchestrator's failure branch discarded the failing worker's `reason` when converting to the
pipeline-level result. Fixed by adding the field and threading it through. Finding this is what
surfaced the *next* bug, immediately below.

**Hit a real Gemini free-tier quota limit mid-session** (20 requests/day) from the volume of live
testing this build involved. Confirmed via the actual API error text, not a guess — and confirms
the hard-failure path (retry-with-backoff, then a clean reported failure rather than a crash)
works exactly as designed under a real failure, not just a simulated one.

**The Rupee currency symbol rendered as a garbled block character.** Building a more diverse,
hand-verified test set meant generating new invoices in multiple currencies via a PDF library
(reportlab). The ₹ symbol came out as a solid black box instead of the actual glyph — reportlab's
default PDF fonts use an encoding that covers $/€/£ but not that particular Unicode character.
Caught by actually opening the rendered PDF and reading it, not by trusting the generation
script's own variables — which is the entire point of "hand-verified." Fixed by using "Rs."
instead of relying on the symbol glyph.

## Evaluation results

Test set: **17 hand-verified documents** (14 invoices, 3 receipts), up from the original 5 — now
spanning 3 distinct visual templates, 4 currencies, varied optional-field combinations, one
deliberate date-order-warning case, and 3 deliberately blurred/rotated/noisy images (the first
time this project's test data has actually exercised the "ambiguous/unreadable" extraction-status
path, rather than only clean digital PDFs).

_Numbers pending a live re-run — the day's Gemini free-tier quota was exhausted by this session's
own testing volume. Everything that doesn't require the live API is independently verified: the
scoring logic, the file-discovery/ground-truth-matching, and the rendering are all covered by unit
tests and a dry run. Original 5-invoice result, for reference: 100% extraction success, 100% field
accuracy._

Honest caveat, not a boast, whatever the refreshed numbers say: every document in this set is
either a digitally-generated PDF or a deliberately-degraded image built from one — not an actual
scan from a real business. A high score says the pipeline is correct across the range of
formats/currencies/degradations actually tested, not that it's robust to arbitrary real-world
scans (different fonts, handwriting, physical damage).

## Anticipated interview questions

**Why split validation into two layers instead of one function?** Because "malformed" and
"domain-inconsistent" are different failure classes that need different handling — a missing
field has no dependency on anything else, but an arithmetic mismatch could be caused by any of
several fields together. Merging them would make it impossible to say which kind of problem
happened.

**Why is only the Correction Worker agentic?** Every other step either has no decision to make
(extraction, formatting) or must never be decided by the model at all (validation — an LLM
should never decide whether math checks out). The Correction Worker is the one place "how should
I re-examine this" doesn't have one correct fixed answer.

**How do you know the agentic loop is actually bounded, not just "trust the model to stop"?**
Two separate caps: the orchestrator only invokes the Correction Worker once per pipeline run
(`max_correction_rounds=1`), and inside that one invocation, the tool-calling loop itself is
capped at a fixed number of turns. The model decides *within* those bounds, not whether the
bounds exist.

**How do you actually know the architecture generalizes, instead of just saying it does?** I
built a second, deliberately different document type (receipts) on the same engine and required
zero changes to the orchestrator or the structural validator. It also surfaced three real bugs
(a hardcoded retry-group import, a hardcoded kwarg name, two places that found "the item list" by
guessing a field name) that only existed because nothing had tested the generic path against a
genuinely different second shape before. That's a stronger claim than "the code is generic" — it's
"I tried to break the genericity claim and found exactly where it was still lying."

**What would you change with more time?** Test against genuinely messy real-world scans (actual
photographed/scanned invoices, not constructed documents) — that's the real test of whether the
validation and retry logic earn their keep beyond the 3 templates and mild synthetic degradation
built so far.

**What's the actual failure mode this catches that a naive "just call an LLM" version wouldn't?**
A subtotal that doesn't match its line items, or a total that doesn't match subtotal + adjustments
— both get caught and flagged automatically, and specifically re-examined (as a dependency group,
not just the one named field) instead of silently accepted or requiring a full manual re-check.

---

_Last updated: 2026-07-18 at commit 934974d._
