# Tasks — Invoice Intelligence Tool

Build in order. Hand these to Claude Code one at a time — implement, review,
then move on. Check the box once verified by hand, not just "ran without
crashing."

## Day 1 (Fri evening + Sat morning) — skeleton, one invoice end to end

- [ ] T1. Repo setup: git init, virtualenv, `pip install -r requirements.txt`.
- [ ] T2. Review `src/schema.py` against your actual sample invoices — adjust
      fields if needed. This is the Invoice/LineItem Pydantic model only;
      validation logic moves to T6/T7 below, not here.
- [ ] T2a. `schema_registry.py`: already written. Confirms `invoice-v1` is
      registered with `Invoice`, `INVOICE_BUSINESS_RULES`, `RETRY_GROUPS`,
      and required fields. No new work here unless T2 changed field names —
      keep the registry in sync if so.
- [ ] T3. `ingest.py`: load file, detect PDF vs image, render/resize to page
      images, base64 encode. Test on one sample.
- [ ] T4. `extract.py` as the Extraction Worker: prompt embeds the schema
      (looked up via `schema_registry.get_schema("invoice-v1")`, not a
      hardcoded `Invoice` import), calls the vision LLM, parses into the
      registered model, wraps as
      `WorkerResult(status="ok", state={"invoice": ..., "image": ...})`.
      The prompt must instruct the model: for each field, report
      `field_status` (extracted/missing/ambiguous/unreadable) rather than
      guessing when unsure, and a one-line `source_note` (e.g. "table row
      3") for where it read the value — see FR11/FR12, D13/D14. Get ONE
      invoice through as valid JSON. Ignore accuracy for now — just get the
      shape and the WorkerResult wrapping right.
- [ ] T5. `orchestrator.py`: already written (generic `run_pipeline`, takes a
      worker list + optional correction_worker, knows nothing about
      invoices). Wire Extraction Worker into it as the first worker, print
      the result. First end-to-end pass.

## Day 2 (Sat) — the two validation layers, confidence, the one agentic loop, eval

- [ ] T6. `schema_validate.py`: already written, schema-agnostic (takes
      `schema_id`, looks up the model via `schema_registry.py` — never
      imports `Invoice` directly). Structural checks only — required fields
      present, types correct, dates/numbers parse. Wrap as a Validation
      Worker returning `WorkerResult` (status "ok" if clean, "retry" if any
      error flags). Keep separate from T7, do not merge them into one function.
- [ ] T7. `business_validate.py`: already written as discrete rule functions
      (`check_line_items_sum`, `check_total_arithmetic`, `check_date_order`,
      `check_duplicate_invoice_number`) packaged as `INVOICE_BUSINESS_RULES`.
      Domain checks only — line items sum to subtotal, subtotal + tax =
      total, invoice date <= due date, duplicate invoice number within the
      current batch. Same Validation Worker as T6 also runs this layer
      (via the registry's `business_rules` list, not a direct import) and
      merges both flag lists into `state["flags"]`.
- [ ] T8. `confidence.py`: heuristic scoring per field, derived from T6/T7
      results (not from the LLM). Start simple: pass all checks = high,
      fail any = low, no need for a fancy formula yet.
- [ ] T9. `retry.py` as the Correction Worker — the one agentic loop (D11).
      Give the model ONE tool, e.g. `re_read_region(field_group, reason)`,
      built from `business_validate.retry_field_groups()` (the dependency
      group, not just the single named field — see D6). Let the model call
      the tool and decide when it's done, rather than your code deciding in
      advance. `orchestrator.py` already caps this at `max_correction_rounds=1`
      — don't raise that cap even if the model wants to keep going.
      TIME-BOX: if tool-calling isn't converging cleanly after ~1.5-2 hrs,
      fall back to D6's deterministic single-shot retry (still real, still
      correct) and document the tradeoff in the README. Don't lose the
      weekend to this one task.
- [ ] T9a. In `extract.py`: wrap the LLM call with retry-with-backoff (2
      attempts) for hard failures (API error, timeout, unparseable
      response) — separate from T9's agentic correction. If still failing,
      return `WorkerResult(status="failed", ...)` so the orchestrator stops
      cleanly for that invoice and `eval.py` can skip it and continue.
- [ ] T10. `report.py`: group flags into PASS / Warnings / Errors. Keep the
      three signals visibly separate in the report, not merged into one list
      (per D13): field_status, heuristic confidence, business validation
      result. This reads existing data — no new validation logic here.
- [ ] T11. `export.py`: write JSON + CSV including the report grouping.
- [ ] T12. Build the eval set: 5-10 sample invoices, hand-written correct
      values in `tests/ground_truth/`.
- [ ] T13. `eval.py`: field-level accuracy + extraction success rate across
      the set. **Do not skip this — it's the strongest resume signal in the
      whole project.**

## Day 3 (Sun -> Mon) — UI, README, ship

- [ ] T14. `app.py`: Streamlit upload -> run -> show the validation report
      (PASS/Warnings/Errors) with flagged fields visible.
- [ ] T14a. In `app.py`: show the source invoice image alongside the report,
      `st.columns([1,1])` — image left, report right. Show each field's
      `source_note` next to its value (e.g. "Subtotal: $240.00 — table row
      3") — this is your citation-level grounding, per D14. Required for
      the tool to actually be reviewable. ~25-30 min.
- [ ] T15. `README.md` as an engineering doc: Problem Statement,
      Architecture (include the orchestrator/worker contract and WHY it's
      generic — this is a strong differentiator, don't undersell it),
      Pipeline, Design Decisions, Schema Design, Validation Strategy (both
      layers + the three separate signals: status/confidence/business
      validation), the one agentic Correction Worker and why it's the only
      agentic piece, Grounding approach (citation-level, explicitly scoped
      down from pixel-level — cite this as a deliberate, informed tradeoff),
      Evaluation (your actual numbers), Limitations, Future Work (including
      "reusable for other document workflows"), Screenshots, Demo link.
- [ ] T16. Record a 60-90 second demo: upload -> extraction -> validation
      report with at least one flagged field visible.
- [ ] T17. Clean up, push to GitHub, add screenshots to the README.

## Future work (already scoped out of MVP — do not build unless far ahead)

- [ ] S1. Bounding-box highlighting (pixel-level grounding — see D14; this
      project ships citation-level grounding instead).
- [ ] S2. Multi-invoice-per-file support.
- [ ] S3. Jurisdiction-specific rules (GST/VAT).
- [ ] S4. Precision/recall for line-item detection specifically.
- [ ] S5. Typed chunk decomposition (route text/table/figure regions to
      specialized tools before extraction) — only worth revisiting if
      testing reveals real invoices with layouts complex enough that a
      single full-page vision call is unreliable. See design.md "Borrowed
      ideas — Out".
