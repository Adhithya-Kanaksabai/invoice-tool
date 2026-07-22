"""
Unit tests for orchestrator.py — the one generic, reusable piece of this
project. Uses fake workers with no domain knowledge, exercising exactly the
contract orchestrator.py promises: it only ever reads WorkerResult.status.
"""

import time

import pytest

from orchestrator import LATENCY_KEY, WorkerResult, run_pipeline


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
    # The orchestrator now also writes its own reserved timing key into state;
    # everything a worker put there must still be exactly what it put there.
    payload = {k: v for k, v in result.final_state.items() if not k.startswith("_")}
    assert payload == {"a": True, "b": True}


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


# --- per-stage latency instrumentation -------------------------------------


def test_run_pipeline_records_latency_per_worker_name():
    def slow_worker(state):
        time.sleep(0.01)
        return WorkerResult(status="ok", state=state)

    def fast_worker(state):
        return WorkerResult(status="ok", state=state)

    result = run_pipeline({}, workers=[slow_worker, fast_worker])
    timings = result.final_state[LATENCY_KEY]

    assert set(timings) == {"slow_worker", "fast_worker"}
    assert timings["slow_worker"] >= 0.01
    assert timings["slow_worker"] > timings["fast_worker"]


def test_run_pipeline_latency_survives_a_worker_that_rebuilds_state():
    """A worker returning a brand-new dict must not erase earlier timings."""

    def first(state):
        return WorkerResult(status="ok", state=state)

    def rebuilds_state_from_scratch(state):
        return WorkerResult(status="ok", state={"fresh": True})

    result = run_pipeline({}, workers=[first, rebuilds_state_from_scratch])
    assert set(result.final_state[LATENCY_KEY]) == {"first", "rebuilds_state_from_scratch"}


def test_run_pipeline_accumulates_time_for_a_worker_run_twice():
    """
    A validation worker re-run after a correction round should report its
    TOTAL time, mirroring how `history` lists it twice.
    """
    seen = []

    def validator(state):
        seen.append(1)
        time.sleep(0.01)
        return WorkerResult(status="retry" if len(seen) == 1 else "ok", state=state)

    def fixer(state):
        return WorkerResult(status="ok", state=state)

    result = run_pipeline({}, workers=[validator], correction_worker=fixer)
    assert result.history.count("validator") == 2
    assert result.final_state[LATENCY_KEY]["validator"] >= 0.02
    assert "fixer" in result.final_state[LATENCY_KEY]


def test_run_pipeline_records_latency_for_a_failed_worker():
    def failing(state):
        time.sleep(0.01)
        return WorkerResult(status="failed", state=state, reason="boom")

    result = run_pipeline({}, workers=[failing])
    assert result.status == "failed"
    # A failure that took ten seconds is exactly the latency worth seeing.
    assert result.final_state[LATENCY_KEY]["failing"] >= 0.01


def test_run_pipeline_records_latency_even_when_a_worker_raises():
    def exploding(state):
        time.sleep(0.01)
        raise RuntimeError("api timeout")

    state = {}
    with pytest.raises(RuntimeError):
        run_pipeline(state, workers=[exploding])
    assert state[LATENCY_KEY]["exploding"] >= 0.01
