# invoice-tool

Portfolio/interview project: LLM-based invoice + receipt extraction with a generic
orchestrator/worker pipeline, two-layer validation, a bounded agentic correction loop, and a
measured (not guessed) eval pipeline. Defensible engineering decisions matter more here than
feature count — every non-obvious choice should be traceable to a reason.

## Paths that bite

- Claude Code's cwd is `resume/`, one level **above** this repo (`resume/invoice-tool/`).
  Always use paths relative to `invoice-tool/`, not `resume/`.
- venv is at `invoice-tool/venv/Scripts/`. Use `venv/Scripts/python.exe` and
  `venv/Scripts/ruff.exe` explicitly — never a bare `python`/`ruff` (PATH is unreliable across
  shells here).
- Never run the Streamlit app with a bare `streamlit run` — use the `streamlit-app` entry in
  `.claude/launch.json` (via the preview tools), which sets `--server.headless true`.

## Commands

```bash
venv/Scripts/python.exe -m pytest tests/ -q      # 152 passing — keep green
venv/Scripts/ruff.exe check .
venv/Scripts/ruff.exe format --check .
```

Two entrypoints make **real, billed Gemini API calls** — don't loop them, don't run without a
reason:
- `venv/Scripts/python.exe src/eval.py` — ~29 live calls against the hand-verified set.
- `venv/Scripts/python.exe src/cord_eval.py` — 20 live calls against the CORD-v2 benchmark.

## Hard rules

- **Never push to `main`.** Branch protection is enforced. Always: feature branch → commit →
  push → open a PR → stop for the user's review. Never merge without being told to.
- **Refresh `GOD_FILE.md` via the `godfile` skill before any push** — it's the interview-prep
  narrative and needs the new work reflected, not on every commit, only at push time or on
  request.
- **`eval.py` and the orchestrator/worker contract: additive changes only.** Don't rebuild
  `run_eval()` or change `WorkerResult{status, state, reason}` — extend, don't rewrite.
- **Never fabricate a number.** Token counts are measured (`usage_metadata` off the real API
  response); cost is *derived* from a published rate and must always carry the rate + the date
  it was read (see `src/llm_usage.py`). If a rate isn't confirmed, ask — don't guess.

## Architecture, in one paragraph

One generic orchestrator (`src/orchestrator.py`) runs a list of workers; each worker is
`dict -> WorkerResult{status, state, reason}` and the orchestrator never inspects `state`'s
contents. Pipeline: extract (vision LLM) → validate (schema, then business rules) → \[if a
business rule fails: one bounded agentic correction round\] → confidence scoring → report. The
orchestrator never imports `Invoice` or `Receipt` — two schemas (`invoice-v1`, `receipt-v1`) are
registered via `schema_registry.py`, and that's the actual proof the engine generalizes, not
just a claim.

## Where to look for depth (don't read unless you need it)

- **`GOD_FILE.md`** — the full interview narrative: every real bug hit, root cause, fix,
  before/after numbers. Read this before answering "what's a hard problem you solved."
- **`spec/design.md`** — the D1–D17 decision log, e.g. why exactly one agentic loop, why
  validation is two layers, why retry targets a dependency group not a single field.
- **`evals/reports/`** and **`evals/benchmarks/`** — dated JSON+markdown runs, the actual
  measured numbers (not restated here because they go stale — check the latest dated file).

## Compact instructions

If this conversation gets auto-compacted, preserve: the current git branch and any open PR
number, any uncommitted changes in the working tree, and which numbers in the conversation were
*measured* vs *derived/estimated* (see Hard rules above — don't blur that distinction across a
compaction boundary).
