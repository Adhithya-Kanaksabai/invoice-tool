---
name: godfile
description: Maintain GOD_FILE.md, this project's interview-prep narrative (what it does, what problem it solves, what broke and how it got fixed, real eval numbers, anticipated Q&A). Use before every `git push` of this repo to GitHub, and whenever the user asks to update the interview doc / god file / project summary. Not triggered by ordinary code changes — only push-time and explicit request.
---

# Maintaining GOD_FILE.md

`GOD_FILE.md` (repo root, alongside `README.md`) is the plain-language version of this
project for talking through in a campus interview. It is NOT the engineering reference —
that's `README.md` (architecture-level) and `spec/design.md` (the full decision log,
D1–D16+). GOD_FILE.md is "what I'd actually say out loud," written so the user can read
it once and rehearse from it, not skim it as documentation.

## When to run this

- Immediately before any `git push` of this repo to GitHub (proactively — don't wait to be
  asked, but always tell the user you're refreshing it as part of the push).
- Whenever the user explicitly asks to update the god file / interview doc / project summary.
- Never on ordinary code changes alone. If neither trigger applies, don't run this.

## Procedure

1. **Read the existing `GOD_FILE.md`** if it exists. Its footer records the git commit hash
   it was last written against — note that hash.

2. **Gather what's new since that hash**:
   - `git log <last-hash>..HEAD --oneline` (or full history if no prior file exists yet)
   - `git diff <last-hash>..HEAD --stat` for what actually changed
   - Re-skim `spec/design.md`'s decision log for any D-numbered decision not yet reflected
   - Re-skim this session's own recent work (bug fixes, tool changes, test additions) —
     the "problems encountered" section is the most valuable part of this file and easy to
     under-fill if you only read git diffs instead of remembering what actually went wrong.

3. **Rewrite `GOD_FILE.md` in full** (don't just append) with these sections, in plain
   language — imagine explaining this out loud to an interviewer who hasn't read the code:

   - **Elevator pitch** — one paragraph: what the tool does, why it matters, in plain terms.
   - **Problem it solves** — the manual invoice data-entry pain point in accounts-payable.
   - **Architecture, in plain English** — the orchestrator/worker split and why it's generic,
     without assuming the reader has read `orchestrator.py`.
   - **Key decisions and the reasoning** — condensed from design.md's D-numbers, framed as
     "why X over Y," not just restated.
   - **Problems encountered and how they got fixed** — real incidents, e.g.: the sample
     invoices turned out to have no `tax` field at all (just discount/shipping), which would
     have made every invoice fail validation until the schema and arithmetic rule were
     generalized; a Gemini model name went stale mid-build and had to be swapped for the
     `-latest` alias; the Correction Worker's tool-calling returned corrected numbers as
     currency strings (`"$606.34"`) which silently failed Pydantic validation until adding
     explicit coercion; Poppler needed a PATH workaround on Windows. Keep this section growing
     — add new incidents, never delete old ones (they're the strongest interview material).
   - **Evaluation results** — the actual current numbers from `eval.py`, with the same honest
     caveat as the README (small, clean test set — a correctness signal, not a robustness claim).
   - **Anticipated interview questions** — a short rehearsable Q&A list (e.g. "why two
     validation layers instead of one," "why is only one part of this agentic," "what would
     you change with more time"). Add new ones if a real conversation surfaces a good question.
   - **Footer**: `_Last updated: <date> at commit <hash>_`

4. **Write the result to `invoice-tool/GOD_FILE.md`**, overwriting the previous version.

5. **Stage it** (`git add GOD_FILE.md`) if a commit/push is about to happen — it's meant to
   be committed to the public repo, not gitignored.

## Tone

Write it the way the user would actually talk, not the way a README talks. Short sentences.
Concrete, specific claims backed by real numbers and real bugs — never generic filler like
"implemented robust error handling." If something is a genuine limitation, say so plainly;
that reads as more credible in an interview than overclaiming.
