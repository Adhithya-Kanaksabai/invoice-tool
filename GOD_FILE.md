# GOD_FILE — Invoice Intelligence Tool, interview prep

This is the "what I'd actually say out loud" version of this project. `README.md` is the
engineering reference; `spec/design.md` has the full decision log (D1–D16). This file is for
talking through the project in an interview, not for documenting the code.

## Elevator pitch

It's a tool that takes a scanned invoice — a PDF or an image — and turns it into structured,
validated data: vendor, customer, invoice number, dates, line items, totals. Instead of trusting
whatever a vision LLM says, it runs the extraction through two layers of validation, catches
arithmetic and structural problems automatically, and only asks a human to look at the specific
fields that are actually in question — not re-check the whole document by hand.

## Problem it solves

In accounts-payable, someone reads a scanned invoice and retypes it into a system by hand —
vendor, dates, every line item, totals. It's slow and error-prone, and the retyping itself adds
no value; the value is in catching mistakes. This tool automates the transcription and turns the
human's job into "confirm these three flagged fields," not "retype the whole invoice."

## Architecture, in plain English

The pipeline is one **generic orchestrator** running a list of **workers**, where invoices are the
first workflow built on top of it. Every worker is a plain function: it takes the current state
(a dict) and returns a `WorkerResult` — a status (`ok`, `retry`, or `failed`), the updated state,
and an optional reason. The orchestrator only ever looks at that status. It never imports the
`Invoice` type, never knows what a subtotal is, never contains a business rule.

Why bother with that split instead of just writing one pipeline function? Because it means the
*only* thing that's "generic" and reusable is the handoff shape between steps — not the workers
themselves. If a second document type shows up later (a receipt, a purchase order), it gets its
own extraction/validation/retry workers; only the shape of `WorkerResult` is shared, nothing
invoice-specific leaks into the orchestrator. That's a deliberately narrow "reusable engine" — no
speculative plugin system or config DSL for workflows that don't exist yet.

The actual pipeline, in order: ingest the file into page images → extract with a vision LLM
(Gemini) → validate structurally (schema) → validate against business rules (arithmetic, dates,
duplicates) → if something failed, hand off to a Correction Worker that re-examines just the
affected fields → re-validate → build a PASS/Warnings/Errors report → export as JSON/CSV, and
show it all in a small Streamlit UI with the source image next to the output.

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
  "decides for itself" doesn't become "loops forever."

- **A registry, not hardcoded invoice knowledge.** A `schema_registry` maps a schema ID
  (`"invoice-v1"`) to its model, business rules, and retry groups. Before this, `Invoice` was
  imported by name in three different files. Now the validation and confidence code never
  imports `Invoice` directly — they take a schema ID and look everything up. Adding a second
  document type later means registering one new entry, not editing the generic path.

- **Citation, not bounding boxes.** Real grounding — cropping the exact region a value came from
  — needs a layout model, which is real infrastructure with no guarantee of working reliably on
  arbitrary invoices in a weekend. What ships instead: the full source image next to the output,
  plus a short text note from the model on where it read each value (e.g. "summary section,
  Subtotal row"). That's a deliberate, named simplification, not a silent shortcut.

## Problems encountered and how they got fixed

**The schema didn't match the real invoices.** The spec assumed every invoice has a `tax` field
and that `subtotal + tax = total`. All 5 real sample invoices turned out to be one generated
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

## Evaluation results

Running `eval.py` over all 5 sample invoices, comparing against hand-verified ground truth:

- **Extraction success rate: 100%** — every invoice completed the pipeline without a hard failure.
- **Field-level accuracy: 100%** — every scalar field and line item matched ground truth within
  tolerance.

Honest caveat, not a boast: this is a small (5-invoice), uniformly clean test set — digitally
generated PDFs from one template, one line item each, no blur, no skew, no handwriting. 100% here
says the pipeline is correct on well-formed input, not that it's robust to messy real-world scans.
That's exactly why the two-layer validation and the Correction Worker exist — production invoices
won't look this clean, and the point of this project is having a real, tested mechanism for when
they aren't.

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

**What would you change with more time?** Test against a genuinely messy, varied invoice set
(different vendors, blur, handwriting, multi-page) instead of one clean template — that's the
real test of whether the validation and retry logic earn their keep. Also build a second document
type (a receipt) to actually prove the orchestrator/worker contract generalizes, rather than
asserting it does from a sample size of one.

**What's the actual failure mode this catches that a naive "just call an LLM" version wouldn't?**
A subtotal that doesn't match its line items, or a total that doesn't match subtotal + adjustments
— both get caught and flagged automatically, and specifically re-examined (as a dependency group,
not just the one named field) instead of silently accepted or requiring a full manual re-check.

---

_Last updated: 2026-07-18 at commit 29d4363 (plus uncommitted Ruff/pytest/skill additions from
the same session, pending the next commit)._
