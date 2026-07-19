"""
orchestrator.py — the generic orchestration layer.

This file must never import Invoice, never know what "subtotal" means, and
never contain a business rule. Its entire job is: run a list of workers in
order, pass an opaque state dict between them, and decide continue/retry/stop
based on WorkerResult.status.

This is the ONE reusable surface of this project if a second workflow
(SpecFlow or otherwise) gets built later. Everything domain-specific belongs
inside a worker, behind the WorkerResult boundary — not here.

Deliberately NOT built: a config system, a plugin registry, a workflow
definition DSL. Those would be generalizing for a second use case that
doesn't exist yet. See design.md "Orchestration philosophy".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class WorkerResult:
    status: str  # "ok" | "retry" | "failed"
    state: dict
    reason: str | None = None


# A worker is any callable: dict -> WorkerResult. That's the whole contract.
Worker = Callable[[dict], WorkerResult]


@dataclass
class PipelineResult:
    final_state: dict
    status: str  # "ok" | "failed"
    history: list[str] = field(default_factory=list)  # worker names run, for debugging/eval
    reason: str | None = None  # carried over from the failing WorkerResult, if status == "failed"


def run_pipeline(
    initial_state: dict,
    workers: list[Worker],
    correction_worker: Worker | None = None,
    max_correction_rounds: int = 1,
) -> PipelineResult:
    """
    Run `workers` in order against a shared, opaque state dict.

    - status == "ok"     -> continue to the next worker
    - status == "retry"  -> hand off to `correction_worker` (if provided),
                             capped at `max_correction_rounds`, then continue
                             the SAME step again (re-run the worker that
                             flagged retry, to re-validate the corrected state)
    - status == "failed" -> stop immediately, return failure

    The orchestrator does not know what "retry" means for a given worker —
    it just knows to call correction_worker and try again, bounded.
    """
    state = initial_state
    history: list[str] = []
    correction_rounds = 0

    i = 0
    while i < len(workers):
        worker = workers[i]
        result = worker(state)
        state = result.state
        history.append(getattr(worker, "__name__", f"worker_{i}"))

        if result.status == "failed":
            return PipelineResult(
                final_state=state, status="failed", history=history, reason=result.reason
            )

        if result.status == "retry":
            if correction_worker is None or correction_rounds >= max_correction_rounds:
                # Out of corrections — accept current state, move on with
                # whatever flags exist. Downstream (Report Worker) surfaces
                # this as "unresolved" rather than looping forever.
                i += 1
                continue

            correction_rounds += 1
            correction_result = correction_worker(state)
            state = correction_result.state
            history.append(getattr(correction_worker, "__name__", "correction_worker"))
            # Re-run the SAME worker (e.g. Validation Worker) to check
            # whether the correction actually resolved the issue.
            continue

        # status == "ok"
        i += 1

    return PipelineResult(final_state=state, status="ok", history=history)
