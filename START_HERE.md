# Start here (when you're fresh)

## 0. Get this folder onto your machine
    ~/projects/invoice-tool

## 1. Drop in 2-3 sample invoices
    tests/sample_invoices/

## 1a. Get a Gemini API key
Get a free API key at https://aistudio.google.com/apikey (covered under your
Google Student Pro plan). Set it as an env var before running anything:
    export GEMINI_API_KEY="your-key-here"
Do NOT commit the key. Add a `.env` or export it in your shell profile.

## 2. Open the folder in Claude Code
    cd ~/projects/invoice-tool
    claude

## 3. Say this first
    Read spec/requirements.md, spec/design.md (especially "Orchestration
    philosophy", "Borrowed ideas", and D15 on the schema registry), and
    spec/tasks.md. Then implement task T1 from tasks.md.

Then "now T2", "now T3", etc. Review each result before moving on.

## 4. When you hit a [DECISION NEEDED]
Tell Claude Code your choice in plain English and have it update the doc.

## Folder map
    invoice-tool/
      spec/
        requirements.md   <- what + why, non-goals, eval metrics
        design.md         <- how, orchestration philosophy, the 12 key decisions
        tasks.md           <- ordered build checklist, Fri->Mon
      src/
        orchestrator.py       <- generic orchestrator, ALREADY WRITTEN. Knows
                                  nothing about invoices. Read design.md's
                                  "Orchestration philosophy" section first.
        schema_registry.py    <- ALREADY WRITTEN. Looks up model + business
                                  rules + retry groups by schema_id. The one
                                  place "invoice-v1" is named.
        schema.py             <- Pydantic models only (the contract)
        schema_validate.py    <- Validation Worker, layer 1 (structural),
                                  schema-agnostic — takes schema_id, ALREADY WRITTEN
        business_validate.py  <- Validation Worker, layer 2 (business rules),
                                  discrete functions, ALREADY WRITTEN
                                  + RETRY_GROUPS for the Correction Worker
        confidence.py         <- heuristic confidence (not LLM self-reported)
        (ingest.py, extract.py, retry.py, report.py, export.py, app.py,
         eval.py — build these per tasks.md, wrapped as Workers returning
         WorkerResult per orchestrator.py's contract)
      tests/
        sample_invoices/
        ground_truth/
      requirements.txt

## What changed from the first draft (context for future you)
- Validation split into two layers (schema vs business) — don't merge them.
- Confidence is computed from retry/validation signals, not asked from the LLM.
- Architecture is now orchestrator + workers (generic contract, invoice-specific
  workers) instead of a flat pipeline.py — orchestrator.py never imports
  Invoice. This is the "reusable engine" seam, kept deliberately thin (see
  design.md D12) — no speculative config system for workflows that don't exist yet.
- Exactly ONE agentic loop: the Correction Worker (retry.py) uses real
  tool-calling and lets the model decide when it's done, capped at 1 round.
  Everything else (extraction, validation, report) stays deterministic on
  purpose — see design.md D11 for why each step was or wasn't a fit.
- Borrowed 3 ideas from a Document AI course (see design.md "Borrowed
  ideas"): explicit per-field status (extracted/missing/ambiguous/
  unreadable) instead of forcing every field to a value; three separate
  signals kept visible (status, confidence, business validation — none
  alone proves correctness); citation-level grounding (source_note text +
  page image) as the achievable version of the course's pixel-level
  bounding-box grounding. Everything RAG/AWS/production-scale from that
  course was deliberately left out — see design.md for the full list and why.
- Added `schema_registry.py` (D15): invoice is now the first registered
  schema, not a hardcoded assumption. `schema_validate.py` takes a
  `schema_id` and is fully schema-agnostic. `business_validate.py`'s rules
  are discrete functions packaged as `INVOICE_BUSINESS_RULES` for the
  registry. Explicitly did NOT build a generic field-type wrapper, a
  business-rule DSL, or a generic prompt-builder — see D15 for why each
  would be solving a problem this project doesn't have yet.
- Streamlit only, no React.
- No bounding boxes, no GST rules, no precision/recall — deferred to Future Work.
- Eval = field accuracy + extraction success rate only.
- Vision provider: Gemini (free under Google Student Pro), not Claude/GPT-4o.

## Model to use inside Claude Code
Default to Sonnet unless you hit something gnarly.
