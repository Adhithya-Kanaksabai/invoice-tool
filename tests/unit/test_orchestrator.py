"""
Unit tests for orchestrator.py — the one generic, reusable piece of this
project. Uses fake workers with no domain knowledge, exercising exactly the
contract orchestrator.py promises: it only ever reads WorkerResult.status.
"""

from orchestrator import WorkerResult, run_pipeline


def test_run_pipeline_all_ok_runs_every_worker_in_order():
    calls = []

    def worker_a(state):
        calls.append("a")
        return WorkerResult(status="ok", state={**state, "a": True})

    def worker_b(state):
        calls.append("b")
        return WorkerResult(status="ok", state={**state, "b": True})

    result = run_pipeline({}, workers=[worker_a, worker_b])
    assert calls == ["a", "b"]
    assert result.status == "ok"
    assert result.final_state == {"a": True, "b": True}


def test_run_pipeline_stops_immediately_on_failed():
    def worker_a(state):
        return WorkerResult(status="failed", state=state, reason="boom")

    def worker_b(state):
        raise AssertionError("should never be called after a failed status")

    result = run_pipeline({}, workers=[worker_a, worker_b])
    assert result.status == "failed"
    assert result.history == ["worker_a"]


def test_run_pipeline_retry_calls_correction_worker_then_rechecks_same_worker():
    call_log = []

    def flaky_validator(state):
        call_log.append("validate")
        if state.get("fixed"):
            return WorkerResult(status="ok", state=state)
        return WorkerResult(status="retry", state=state, reason="needs fixing")

    def fixer(state):
        call_log.append("fix")
        return WorkerResult(status="ok", state={**state, "fixed": True})

    result = run_pipeline(
        {}, workers=[flaky_validator], correction_worker=fixer, max_correction_rounds=1
    )
    assert call_log == ["validate", "fix", "validate"]
    assert result.status == "ok"
    assert result.final_state["fixed"] is True


def test_run_pipeline_respects_max_correction_rounds_and_moves_on():
    call_log = []

    def always_retry(state):
        call_log.append("validate")
        return WorkerResult(status="retry", state=state, reason="never fixed")

    def fixer(state):
        call_log.append("fix")
        return WorkerResult(status="ok", state=state)

    result = run_pipeline(
        {}, workers=[always_retry], correction_worker=fixer, max_correction_rounds=1
    )
    # One correction round is spent, then the orchestrator accepts the
    # still-flagged state rather than looping forever (per D6/D11).
    assert call_log == ["validate", "fix", "validate"]
    assert result.status == "ok"


def test_run_pipeline_retry_without_correction_worker_moves_on():
    result = run_pipeline(
        {},
        workers=[
            lambda state: WorkerResult(status="retry", state=state, reason="no fixer available")
        ],
    )
    assert result.status == "ok"
