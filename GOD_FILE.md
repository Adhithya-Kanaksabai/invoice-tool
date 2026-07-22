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

Underneath that, every run is now persisted to a real database (SQLAlchemy models, Alembic
migrations) — not just shown once and discarded. That backs two real features, not just a log:
a content-hash cache (re-uploading the exact same file bytes skips a fresh Gemini call entirely —
a genuine cost/quota saving, verified live: cache hit in well under a second vs. several seconds
for a real extraction), and cross-run duplicate detection (an invoice number that already exists
from a *previous* session, not just earlier in the same batch). Persistence failures are
surfaced loudly, never swallowed — a save failing doesn't block showing the result that already
succeeded, but it's not silently dropped either.

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
works exactly as designed under a real failure, not just a simulated one. Turned out not to be a
fluke of my usage — Google cut `gemini-2.5-flash`'s (what `gemini-flash-latest` resolved to at the
time) free-tier quota from 250 requests/day down to 20, platform-wide, without notice, in December
2025. Rather than switch providers entirely, checked `client.models.list()` for what was actually
available on the same API key and found `gemini-3.1-flash-lite` — a newer model with a much higher
free-tier ceiling. Switched via the `GEMINI_MODEL` environment variable, which `extract.py` and
`retry.py` already read — zero code changes, confirming the "provider/model swap is a config
change, not a rewrite" design decision actually held under a real forcing event, not just in
theory. Verified both call sites still work post-switch: a plain extraction call, and a
function-calling round-trip (needed by the Correction Worker specifically, since that's a
different capability than the main extraction call uses).

**The Rupee currency symbol rendered as a garbled block character.** Building a more diverse,
hand-verified test set meant generating new invoices in multiple currencies via a PDF library
(reportlab). The ₹ symbol came out as a solid black box instead of the actual glyph — reportlab's
default PDF fonts use an encoding that covers $/€/£ but not that particular Unicode character.
Caught by actually opening the rendered PDF and reading it, not by trusting the generation
script's own variables — which is the entire point of "hand-verified." Fixed by using "Rs."
instead of relying on the symbol glyph.

**Built an independent OCR cross-check, measured it, and removed it — a "no" is still a finding.**
After growing the test set with real-world documents (phone-photo receipts, varied real templates
— see Evaluation below), the natural next step for confidence scoring was an actually *independent*
second reading of the document, not just another interpretation from the same model. Built a
worker that ran Tesseract over the same page images and cross-checked the model's extracted
`total`/id/name/currency fields against Tesseract's raw text, feeding disagreement into confidence.
Wrote `tune_confidence.py` — a script that runs the pipeline against ground truth and correlates
confidence scores with actual correctness — to test whether it worked, rather than assuming it did.
It didn't: **0% catch rate at every threshold tested, across 308 real field observations.** The most
interesting specific failure: one receipt had two different printed numbers ("Order #: 4876" and
"Ticket #: 56"); the model extracted the wrong one, and the OCR cross-check reported a **false
"agrees"** — because "56" was genuinely present on the page too, just as the *other* field's value,
not confirmation of the right one. Naive substring matching can't distinguish "this string appears
somewhere on the page" from "this string is the right field's value" — a real limitation, not a bug
to patch trivially. Separately tried to fix a related rotation blind spot (Tesseract returned empty
text on the two most-degraded real documents) via a projection-profile deskew; measured it directly
and found it detected 0° rotation on both known-rotated test images — zero actual correction — so
that fix was scrapped too, rather than shipped as something that measurably did nothing. Removed
the whole feature (deleted the module and its tests, reverted every wiring point) instead of
leaving non-functional code in the pipeline. The lesson: an "independent" cross-check needs a
reader that's genuinely at least as reliable as what it's checking — Tesseract on real-world photos
isn't. A better independent signal for a future round: sample the vision model itself 2-3× and
treat disagreement across samples as the signal, instead of pairing it with a strictly weaker
non-independent-enough reader.

**Manual adversarial testing (uploading things that aren't invoices at all) surfaced five real
bugs the automated eval set never would have.** The 29-document eval set is all plausible
invoices/receipts by construction — it can't test what happens when a user uploads something
completely wrong. Doing that by hand found:

1. **A raw Pydantic `ValidationError` was shown straight to the user.** Uploading an unrelated
   PDF (a resume) made extraction fail after 3 retries, and the failure message was a literal
   multi-line dump: `"6 validation errors for Invoice / vendor_name / Input should be a valid
   string [type=string_type...]"`. The exception was caught generically (`except Exception as e`)
   and stringified directly into the UI with zero translation. Fixed by parsing which fields were
   missing/malformed out of the `ValidationError` and building one short sentence instead — e.g.
   `"Could not extract a valid Invoice from this document — the model's output was missing or
   malformed for: customer_name."` Verified live: same resume-shaped test file now produces a
   clean one-line message.

2. **No check that the uploaded document is even the right document type.** Uploaded a CBSE
   Class XII marksheet with "Invoice" selected — Gemini hallucinated a field mapping (school name
   into `vendor_name`, etc.), it happened to satisfy Pydantic's type checks, and the pipeline
   reported a clean success with zero warnings. Root cause: the extraction prompt never told the
   model what document type to expect, and nothing downstream checked plausibility, only
   structural validity. Fixed by adding a `document_type_match` / `document_type_note` pair the
   model sets itself (the same "explicit uncertainty signal" pattern `field_status` already uses,
   just at the document level instead of per-field), checked by `validate.py` *before* any
   schema/business validation runs — a document-type mismatch short-circuits straight to a clean
   failure instead of running business rules on data that shouldn't exist. Verified live: the same
   marksheet-shaped test image now fails cleanly instead of reporting fabricated success.

3. **A genuine invoice tripped a real, previously-undetected line-item bug.** A real invoice with
   both a tax-exclusive and tax-inclusive amount column per line item got its line items extracted
   from the *wrong* column — the model used the VAT-inclusive "Total amount" instead of the
   VAT-exclusive "Net amount," so line items summed to the grand total instead of the subtotal
   (off by exactly the VAT amount). Root cause: `LineItem.amount`'s docstring said only "quantity
   × unit_price, as printed on the invoice" — no guidance on *which* printed column to use when an
   invoice shows more than one. Fixed by making both the schema comment and the extraction prompt
   explicit: `amount` must be the pre-tax/net figure, consistent with how `subtotal` and the
   arithmetic check already assume pre-tax math.

4. **The Agentic Correction Worker panel lied about whether it ran.** A genuinely hard receipt
   (a deliberately garbled AI-generated test image) triggered a real validation error, and the
   Pipeline Stages row correctly showed "🤖 Agentic correction" ran — but the Correction Worker
   panel below it said "Not needed — passed validation on the first pass," which was false; it
   *had* run and simply couldn't produce a usable correction (neither the tool-calling loop nor
   the deterministic fallback converged). Root cause: `retry.py`'s give-up path returned the
   unchanged state with no signal that an attempt had happened at all, so the UI's only check
   (`final_state.get("retried_fields")`, which only gets set on *success*) couldn't tell "never
   needed" apart from "tried and gave up." Fixed by tagging
   `correction_attempted_but_failed` + a reason in state on both give-up paths, and giving the UI
   a third state — "Attempted — could not resolve, original values kept" — instead of collapsing
   two different outcomes into the same misleading badge.

5. **The content-hash cache blocked re-testing the exact fixes above.** Once #1–#2 were fixed and
   re-uploading the *same* resume/marksheet files (needed to verify the fix against the exact
   same input), the cache correctly reused the pre-fix cached result instead of calling the model
   again — correct behavior for a real user, actively unhelpful for iterating on a fix. Added a
   "Force re-extraction (skip cache)" checkbox in the UI, wired to the `skip_cache` state key
   `eval.py` already used internally — no new mechanism, just exposing an existing one.

**A raw traceback leaked on the live deployment because the fix for it hadn't actually shipped
yet.** After deploying to Streamlit Community Cloud, uploading a sample PDF threw
`pdf2image.exceptions.PDFInfoNotInstalledError` straight into the UI as a full traceback — the
exact class of bug #1 above (raw exception dumped to the user) but in a place that hadn't been
patched: `extract.py`'s graceful-error wrapping only covered the vision-model call
(`_describe_last_error`), not the file-ingest step above it (`load_page_images()`), which ran
completely outside any try/except. Root cause of *why* Poppler was missing at all: the PR adding
`packages.txt` (Streamlit Cloud's mechanism for installing apt-level dependencies like Poppler)
had been opened but not yet merged to `main` — the live deployment was still running the
pre-fix commit. Two separate fixes: merged the pending PR for the actual Poppler installation,
and closed the ingest-layer gap itself by wrapping `load_page_images()` in `extraction_worker`
with the same "hard failure → clean `WorkerResult`" pattern already used for the extraction call,
plus a new `_describe_ingest_error()` helper that recognizes a missing-Poppler exception by class
name specifically (so the fix holds even if this exact dependency issue recurs) and falls back to
a generic "file appears to be corrupt or unreadable" message otherwise — never the raw exception
text or a traceback. Verified by reproducing the exact failure against the live deployed app
first, not assuming the fix would work.

**A second raw traceback on the same redeploy: an unguarded `os.environ["GEMINI_API_KEY"]`
lookup crashed with a bare `KeyError`.** After merging the Poppler fix above and redeploying,
the PDF ingest step worked, but the very next line — constructing the Gemini client — threw an
uncaught `KeyError` straight into the UI, because `extract.py` and `retry.py` both did
`os.environ["GEMINI_API_KEY"]` unguarded, outside any try/except (in `extract.py`, one line
*before* the try/except block that already wraps everything after it). The orchestrator itself
has no top-level exception handling by design (see "Architecture" above — it only understands
`WorkerResult.status`), so any worker-level crash always reaches the UI raw unless the worker
catches it first; this was simply a spot that hadn't been. Fixed by wrapping both client
constructions in `try/except KeyError`: `extraction_worker` now returns a clean failed
`WorkerResult` ("server misconfiguration"), and `correction_worker` — whose return status the
orchestrator doesn't even check, it only reads `.state` — follows the exact same
`correction_attempted_but_failed` give-up pattern already used for its other two failure paths,
so a missing key mid-correction degrades to "kept original values" instead of crashing the whole
pipeline. Reinforces the same lesson as bug #1 and the Poppler bug above it: every external
dependency a worker touches (the vision API, the filesystem, an env var) needs its own explicit
failure boundary — "wrap the one call I was thinking about" isn't the same guarantee as "nothing
in this worker can throw past its own return."

**A third redeploy, a third bug: extraction finally worked, but persisting the result failed —
`sqlite3.OperationalError: no such table: pipeline_runs`.** With Poppler and the API key both
fixed, a real end-to-end extraction succeeded on the live app for the first time — but saving it
threw a raw SQL error (caught by `app.py`'s own try/except around persistence, so at least not a
full traceback this time, but still leaking a raw exception string with SQL parameters into the
UI). Root cause: this project's schema is created via Alembic (`alembic upgrade head`) — run
manually in local dev, and baked into the Docker image's `CMD` — but Streamlit Community Cloud
has no pre-start hook at all, it just executes `app.py` directly. A fresh ephemeral SQLite file
there had literally never had its tables created. The fix already existed and was never wired
up: `db.py` has an `init_db()` docstring-labeled "dev/test convenience: create tables directly
from the models, no migration history" — calling `Base.metadata.create_all(engine)`, which is
idempotent (checks existing tables first). Added one line, `init_db()`, near the top of
`app.py`, right after the secrets bridge — a safe no-op anywhere Alembic already ran (local dev,
Docker), and the actual fix on Streamlit Cloud where nothing else ever would. Three consecutive
raw-error redeploys in a row (Poppler → API key → DB schema) is itself the finding worth keeping:
each one was a real dependency the app has on its runtime environment that local dev and Docker
both silently satisfy for you, which is exactly why they never surfaced until a genuinely
different deployment target was tried.

**Docker + docker-compose (Postgres), verified end-to-end, not just written and assumed to work.**
Added a `Dockerfile` (Python 3.11-slim, `apt-get install poppler-utils` baked in — the actual
justification for Docker here, since a Poppler-not-on-PATH mid-session install was a real earlier
debugging cost) and a `docker-compose.yml` (app + Postgres 16, healthcheck-gated startup so
Alembic never races an unready DB). Actually ran `docker compose up --build`, confirmed
`alembic upgrade head` created all four tables against a fresh real Postgres container (not
SQLite), ran the full pipeline against a live Gemini call inside the container, and confirmed the
extracted row landed in Postgres with the real values (vendor, invoice number, total) — then
re-ran the same invoice and confirmed cross-run duplicate detection fired correctly against that
same Postgres instance. `db.py`'s "DATABASE_URL from env, SQLite default" design meant this needed
zero code changes — the same "config, not code" shape the Gemini model swap already proved once.

## Evaluation results

Test set: **29 hand-verified documents** (24 invoices, 5 receipts), up from the original 5 — now
spanning multiple distinct visual templates, several currencies, varied optional-field
combinations, one deliberate date-order-warning case, several deliberately blurred/rotated/noisy
synthetic images, AND (new this round) **real-world documents**: genuine phone-photo receipts
(skewed, glare, background clutter) and a range of real invoice templates pulled from the web —
not just synthetically degraded PDFs.

**Full run, all 29 documents, on `gemini-3.1-flash-lite`:**

```
Overall extraction success rate: 100.0%  (29/29)
Overall field-level accuracy:    99.1%

invoice-v1: extraction success 100.0%  field accuracy 99.6%  (24/24)
receipt-v1: extraction success 100.0%  field accuracy 96.8%  (5/5)
```

The gap from 100% (the old all-synthetic number) to 99.1% is the actual point: **real-world
documents produce real errors that synthetic degradation never did.** Every miss traced to a
specific, understood cause — not noise: a receipt with a two-line header creating genuine
merchant-name ambiguity (institution name vs. specific outlet), a stock marketing template whose
own printed subtotal/total don't reconcile with its own line items (decorative placeholder numbers,
not a real transaction), and — the one genuine extraction bug found — a receipt with two different
printed numbers where the model grabbed the wrong one. That bug is also what motivated and then
sank the OCR cross-check attempt (see above): even a feature built specifically to catch this kind
of error gave a false "agrees" on it.

One document (`gen_invoice_INV-1006.pdf`) raised a business-validation warning despite scoring
100% on field accuracy against ground truth — a real, small example of why the two checks are kept
separate: field accuracy asks "did the extracted values match reality," business validation asks
"are the values internally consistent with each other." A document can be right and still trip a
consistency flag (or, in principle, the reverse), and conflating the two into one score would have
hidden that.

Honest caveat, not a boast: even with real-world documents now in the mix, this is still a small,
hand-curated set (29 documents), not a standard public benchmark. The field accuracy number is a
correctness signal on the specific range of formats/degradations actually tested, not a claim of
general robustness — and it's a small enough error count (roughly 3 wrong fields out of ~300+
observations) that no confidence threshold could be meaningfully tuned from it this round.

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

**How did you test failure cases the eval set couldn't catch?** By hand — deliberately uploading
things that aren't invoices at all (a resume, a school marksheet) and a genuinely hard-to-read
receipt. That single session found five real bugs a clean 29-document eval set structurally can't
surface: a raw Pydantic exception dumped straight to the UI, zero document-type plausibility
checking (a marksheet got hallucination-mapped into a "successful" invoice), a real line-item
column-selection bug on an actual invoice, and a UI panel that misreported whether the agentic
correction step had actually run. The eval set proves accuracy on documents that are the right
shape; it says nothing about what happens when they're not — that needs someone actually trying to
break it.

**You built an OCR cross-check — where is it?** Removed it. I tested it against real diverse
documents with a dedicated tuning script rather than assuming it worked, and it had a 0% catch
rate on real errors across 308 field observations, plus a specific false-reassurance failure mode
(agreeing with a wrong value that happened to also appear elsewhere on the same page). I also tried
fixing a related rotation blind spot and measured that the fix did nothing either. Rather than ship
a feature that doesn't earn its keep, I deleted it and documented why — the finding (an independent
signal needs a reader that's actually as reliable as what it's checking, and Tesseract on real-world
photos isn't) is worth more than the feature would have been.

---

_Last updated: 2026-07-22 — includes the SQLAlchemy/Alembic persistence layer (content-hash cache,
cross-run duplicate detection), a Streamlit styling pass, five bugs found via manual adversarial
testing (graceful failure messages, document-type mismatch detection, a real line-item column bug,
and an Agentic Correction Worker UI mislabel), a docs polish pass (README rewritten from 410 to
~130 lines with deep reasoning moved to this file and `spec/design.md` rather than duplicated, an
MIT LICENSE added, and real screenshots captured via a one-off Playwright script since the running
app couldn't otherwise produce savable image files), Docker/docker-compose with Postgres, verified
end-to-end against a real container (migrations, a live extraction, and cross-run duplicate
detection all confirmed working, not just assumed), a second README pass trimming ~130 lines
of prose down to ~90 with a Mermaid flowchart replacing the ASCII architecture diagram, Streamlit
Community Cloud deployment support (`packages.txt` for Poppler, secrets bridging), and three
straight adversarial-testing bugs found on the live deployment itself, one per redeploy — a raw
traceback on PDF upload (an unmerged fix plus a real gap in the ingest-layer's own error
handling), a raw `KeyError` from an unguarded `GEMINI_API_KEY` lookup, and finally a raw SQL
error from a database schema that had never been created (Streamlit Cloud has no pre-start hook
to run Alembic, unlike local dev and Docker) — all three now closed, the last one just by wiring
up an `init_db()` helper that already existed but had never been called from `app.py`._
