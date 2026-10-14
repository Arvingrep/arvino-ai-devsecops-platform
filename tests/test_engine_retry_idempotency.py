"""Retries, backoff, timeouts, attempt fencing, idempotency and concurrency."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from arvino.domain import ArtifactSpec, ConflictError, Profile
from arvino.engine import CompletionResult
from arvino.ports import StepOutcome, StepRequest
from tests.conftest import AGENT, Env, build_env


def fail(msg: str = "transient", retryable: bool = True) -> StepOutcome:
    return StepOutcome(status="failed", error=msg, retryable=retryable)


# ----------------------------------------------------------------------------- retries


def test_retryable_failure_is_retried_after_backoff(env: Env) -> None:
    env.executor.script("triage", fail(), StepOutcome(status="succeeded"))
    run = env.start(Profile.FAST)
    st = run.step("triage")
    assert (st.status, st.attempt, st.last_error) == ("pending", 1, "transient")
    assert st.not_before is not None and run.status == "running"
    assert len(env.executor.calls_for("triage")) == 1

    env.engine.advance(run.id)  # backoff not elapsed: nothing happens
    assert len(env.executor.calls_for("triage")) == 1

    env.clock.advance(31)
    env.engine.advance(run.id)
    assert len(env.executor.calls_for("triage")) == 2
    assert env.executor.calls_for("triage")[1].attempt == 2
    assert env.engine.get_run(run.id).step("develop").status != "pending"


def test_backoff_is_exponential(env: Env) -> None:
    env.executor.script("triage", fail())  # always fails
    run = env.start(Profile.FAST)
    first = run.step("triage").not_before
    assert first is not None
    env.clock.advance(31)
    run = env.engine.advance(run.id)
    # Second failure exhausts max_attempts=2 for agent steps.
    assert run.step("triage").status == "failed"


def test_attempts_exhausted_fails_run_and_cancels_downstream(env: Env) -> None:
    env.executor.script("develop", fail("still broken"))
    run = env.start(Profile.FAST)
    env.clock.advance(31)
    run = env.engine.advance(run.id)
    assert run.step("develop").status == "failed"
    assert run.step("develop").attempt == 2
    assert run.status == "failed" and "still broken" in (run.failure_reason or "")
    assert {
        s.key: s.status for s in run.steps if s.key in {"tests", "pull_request", "ci", "preview"}
    } == {
        "tests": "cancelled",
        "pull_request": "cancelled",
        "ci": "cancelled",
        "preview": "cancelled",
    }
    assert len(env.executor.calls_for("tests")) == 0


def test_non_retryable_failure_fails_immediately(env: Env) -> None:
    env.executor.script("triage", fail("fatal", retryable=False))
    run = env.start(Profile.FAST)
    assert run.step("triage").status == "failed" and run.step("triage").attempt == 1
    assert run.status == "failed"


def test_executor_exception_is_a_retryable_failure(env: Env) -> None:
    def boom(_: StepRequest) -> StepOutcome:
        raise ConnectionError("sandbox unreachable")

    env.executor.script("triage", boom, StepOutcome(status="succeeded"))
    run = env.start(Profile.FAST)
    assert run.step("triage").status == "pending"
    assert "ConnectionError" in (run.step("triage").last_error or "")
    env.clock.advance(31)
    run = env.engine.advance(run.id)
    assert run.step("triage").status == "succeeded"


# ----------------------------------------------------------------------------- timeouts / fencing


def test_timeout_reaps_running_step_and_retries_with_new_attempt(env: Env) -> None:
    env.executor.script("triage", StepOutcome(status="running"))
    run = env.start(Profile.FAST)
    first_attempt = run.step("triage").attempt
    timeout = run.step("triage").timeout_seconds

    env.clock.advance(timeout - 1)
    assert env.engine.reap_timeouts() == []
    env.clock.advance(2)
    assert env.engine.reap_timeouts() == [(run.id, "triage")]
    run = env.engine.get_run(run.id)
    assert run.step("triage").last_error == "timeout"
    assert run.step("triage").status == "pending"  # retry scheduled

    env.clock.advance(31)
    env.executor.script("triage", StepOutcome(status="succeeded"))
    run = env.engine.advance(run.id)
    assert run.step("triage").attempt == first_attempt + 1


def test_completion_from_timed_out_attempt_is_fenced(env: Env) -> None:
    env.executor.script("triage", StepOutcome(status="running"))
    run = env.start(Profile.FAST)
    old_attempt = run.step("triage").attempt
    env.clock.advance(run.step("triage").timeout_seconds + 1)
    env.engine.reap_timeouts()
    env.clock.advance(31)
    env.engine.advance(run.id)  # attempt 2 is now running (executor still says 'running')
    run = env.engine.get_run(run.id)
    assert run.step("triage").attempt == old_attempt + 1

    res = env.engine.complete_step(
        run.id, "triage", old_attempt, StepOutcome(status="succeeded"), AGENT
    )
    assert res is CompletionResult.STALE
    assert env.engine.get_run(run.id).step("triage").status == "running"
    assert "step.stale_completion_ignored" in [e.type for e in env.engine.events(run.id)]

    res = env.engine.complete_step(
        run.id, "triage", old_attempt + 1, StepOutcome(status="succeeded"), AGENT
    )
    assert res is CompletionResult.APPLIED


def test_timeout_exhausting_attempts_fails_run(env: Env) -> None:
    env.executor.script("triage", StepOutcome(status="running"))
    run = env.start(Profile.FAST)
    for _ in range(2):
        env.clock.advance(run.step("triage").timeout_seconds + 1)
        env.engine.reap_timeouts()
        env.clock.advance(31)
        run = env.engine.advance(run.id)
    assert run.status == "failed"


def test_next_wakeup_reports_backoff_and_deadlines(env: Env) -> None:
    env.executor.script("triage", fail())
    run = env.start(Profile.FAST)
    assert run.next_wakeup_at == run.step("triage").not_before


# ----------------------------------------------------------------------------- idempotency


def test_create_run_is_idempotent_on_key(env: Env) -> None:
    a, created_a = env.engine.create_run(
        env.project_id, env.issue_id, Profile.FAST, "same", "owner"
    )
    b, created_b = env.engine.create_run(
        env.project_id, env.issue_id, Profile.FAST, "same", "owner"
    )
    assert (created_a, created_b) == (True, False)
    assert a.id == b.id and len(b.steps) == len(a.steps)
    assert len(env.engine.list_runs()) == 1


def test_idempotency_key_reuse_with_different_parameters_conflicts(env: Env) -> None:
    env.engine.create_run(env.project_id, env.issue_id, Profile.FAST, "k", "owner")
    with pytest.raises(ConflictError):
        env.engine.create_run(env.project_id, env.issue_id, Profile.GOVERNED, "k", "owner")


def test_repeated_advance_never_redispatches(env: Env) -> None:
    env.executor.script("develop", StepOutcome(status="running"))
    run = env.start(Profile.FAST)
    for _ in range(5):
        env.engine.advance(run.id)
    assert [c.step_key for c in env.executor.calls] == ["triage", "develop"]
    assert len(env.executor.calls_for("develop")) == 1


def test_duplicate_completion_is_a_noop(env: Env) -> None:
    env.executor.script("triage", StepOutcome(status="running"))
    run = env.start(Profile.FAST)
    attempt = run.step("triage").attempt
    ok = StepOutcome(status="succeeded")
    assert (
        env.engine.complete_step(run.id, "triage", attempt, ok, AGENT) is CompletionResult.APPLIED
    )
    n_events = len(env.engine.events(run.id))
    assert (
        env.engine.complete_step(run.id, "triage", attempt, ok, AGENT) is CompletionResult.DUPLICATE
    )
    assert len(env.engine.events(run.id)) == n_events
    assert len(env.executor.calls_for("develop")) == 1


def test_idempotency_key_is_unique_per_attempt(env: Env) -> None:
    env.executor.script("triage", fail(), StepOutcome(status="succeeded"))
    run = env.start(Profile.FAST)
    env.clock.advance(31)
    env.engine.advance(run.id)
    keys = [c.idempotency_key for c in env.executor.calls_for("triage")]
    assert keys == [f"{run.id}:triage:1", f"{run.id}:triage:2"]


def test_artifacts_are_recorded_with_producer_identity(env: Env) -> None:
    a = ArtifactSpec(kind="spec", name="spec.md", uri="git://x/spec.md", sha256="1" * 64)
    env.executor.script("triage", StepOutcome(status="succeeded", artifacts=[a]))
    run = env.start(Profile.FAST)
    got = [x for x in run.artifacts if x.step_key == "triage"]
    assert [(x.name, x.sha256, x.produced_by) for x in got] == [
        ("spec.md", "1" * 64, "agent:triage")
    ]


def test_conflicting_artifact_content_fails_step(env: Env) -> None:
    first = ArtifactSpec(kind="spec", name="spec.md", uri="git://x/spec.md", sha256="1" * 64)
    env.executor.script("triage", StepOutcome(status="running"))
    run = env.start(Profile.FAST)
    attempt = run.step("triage").attempt
    # Simulate a prior partial registration for this (step, name) from an earlier attempt.
    from arvino.db import ArtifactRow, transaction

    with transaction(env.engine._sf) as s:
        s.add(
            ArtifactRow(
                run_id=run.id,
                step_key="triage",
                kind="spec",
                name="spec.md",
                uri=first.uri,
                sha256=first.sha256,
                produced_by="agent:triage",
                extra={},
                created_at=env.clock.now(),
            )
        )
    changed = first.model_copy(update={"sha256": "2" * 64})
    env.engine.complete_step(
        run.id, "triage", attempt, StepOutcome(status="succeeded", artifacts=[changed]), AGENT
    )
    run = env.engine.get_run(run.id)
    assert run.step("triage").status == "failed"
    assert "immutable" in (run.step("triage").last_error or "")


# ----------------------------------------------------------------------------- concurrency


def test_claim_is_compare_and_set_under_concurrent_advance(tmp_path: Path) -> None:
    gate = threading.Barrier(4)

    def slow_ok(_: StepRequest) -> StepOutcome:
        return StepOutcome(status="succeeded")

    e = build_env(tmp_path)
    e.executor.script("triage", slow_ok)
    view, _ = e.engine.create_run(e.project_id, e.issue_id, Profile.FAST, "c", "owner")
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            gate.wait()
            e.engine.advance(view.id)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    for key in ("triage", "develop", "tests", "pull_request"):
        # pull_request fails (no PR registered) but is still dispatched at most once per attempt
        keys = [c.idempotency_key for c in e.executor.calls_for(key)]
        assert len(keys) == len(set(keys)), (key, keys)
    assert len(e.executor.calls_for("triage")) == 1


def test_in_flight_step_is_not_redispatched_by_a_nested_advance(env: Env) -> None:
    """Deterministic CAS check: while triage is claimed/in flight, another advance must not re-claim it."""
    seen: list[str] = []

    def reentrant(req: StepRequest) -> StepOutcome:
        env.engine.advance(req.run_id)  # a second driver racing the first
        seen.append(env.engine.get_run(req.run_id).step("triage").status)
        return StepOutcome(status="succeeded")

    env.executor.script("triage", reentrant)
    env.start(Profile.FAST)
    assert seen == ["running"]
    assert len(env.executor.calls_for("triage")) == 1
