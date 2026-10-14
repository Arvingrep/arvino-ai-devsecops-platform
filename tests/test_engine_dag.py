"""Dependency ordering, parallel fan-out and fan-in."""

from __future__ import annotations

from arvino.domain import Profile
from arvino.ports import StepOutcome
from tests.conftest import ALL_CHECKS, BASE_CHECKS, SHA, Env


def test_fast_runs_in_dependency_order_to_preview(env: Env) -> None:
    env.executor.script("pull_request", env.pr_script())
    run = env.start(Profile.FAST)
    # Agent steps ran strictly in order, one at a time, and stopped at the CI wait.
    assert [c.step_key for c in env.executor.calls] == ["triage", "develop", "tests", "pull_request"]
    assert run.statuses()["ci"] == "running"
    assert run.status == "running"

    env.webhook_pr(run.id)
    env.checks(run.id, BASE_CHECKS)
    run = env.engine.get_run(run.id)
    assert run.statuses()["merge_gate"] == "succeeded"
    assert run.statuses()["preview"] == "running"
    assert env.promoter.requests[-1].environment == "preview"
    assert env.promoter.requests[-1].revision == SHA

    env.converge("acme-preview")
    run = env.engine.get_run(run.id)
    assert run.status == "succeeded"
    assert all(s == "succeeded" for s in run.statuses().values())


def test_step_does_not_start_until_dependency_succeeds(env: Env) -> None:
    env.executor.script("develop", StepOutcome(status="running"))  # async: stays running
    run = env.start(Profile.FAST)
    assert run.statuses()["develop"] == "running"
    assert run.statuses()["tests"] == "pending"
    assert [c.step_key for c in env.executor.calls] == ["triage", "develop"]
    env.engine.advance(run.id)  # re-advancing must not skip ahead
    assert [c.step_key for c in env.executor.calls] == ["triage", "develop"]


def test_standard_fanout_dispatches_frontend_and_backend_together(env: Env) -> None:
    env.executor.script("frontend", StepOutcome(status="running"))
    env.executor.script("backend", StepOutcome(status="running"))
    run = env.start(Profile.STANDARD)
    st = run.statuses()
    assert st["frontend"] == st["backend"] == "running"  # both in flight concurrently
    assert st["pull_request"] == "pending"


def test_fanin_waits_for_all_parallel_branches(env: Env) -> None:
    env.executor.script("frontend", StepOutcome(status="running"))
    env.executor.script("backend", StepOutcome(status="running"))
    env.executor.script("pull_request", env.pr_script())
    run = env.start(Profile.STANDARD)

    assert env.complete(run.id, "frontend") == "applied"
    assert env.engine.get_run(run.id).statuses()["pull_request"] == "pending"  # backend still running
    assert not env.executor.calls_for("pull_request")

    assert env.complete(run.id, "backend") == "applied"
    assert env.engine.get_run(run.id).statuses()["pull_request"] == "succeeded"
    assert len(env.executor.calls_for("pull_request")) == 1


def test_qa_and_security_run_in_parallel_and_review_waits_for_both(env: Env) -> None:
    env.executor.script("pull_request", env.pr_script())
    env.executor.script("qa", StepOutcome(status="running"))
    env.executor.script("security", StepOutcome(status="running"))
    run = env.start(Profile.STANDARD)
    env.webhook_pr(run.id)
    env.checks(run.id, ALL_CHECKS)
    st = env.engine.get_run(run.id).statuses()
    assert st["qa"] == st["security"] == "running"
    assert st["review"] == "pending"

    env.complete(run.id, "qa")
    assert not env.executor.calls_for("review")
    env.complete(run.id, "security")
    assert len(env.executor.calls_for("review")) == 1


def test_terminal_failure_of_one_branch_cancels_siblings_and_ignores_late_completion(env: Env) -> None:
    env.executor.script("frontend", StepOutcome(status="running"))
    env.executor.script("backend", StepOutcome(status="running"))
    run = env.start(Profile.STANDARD)
    fe_attempt = run.step("frontend").attempt

    assert env.complete(run.id, "backend", "failed", error="boom", retryable=False) == "applied"
    run = env.engine.get_run(run.id)
    assert run.status == "failed"
    assert run.step("backend").status == "failed"
    assert run.step("frontend").status == "cancelled"
    assert run.step("pull_request").status == "cancelled"
    assert "boom" in (run.failure_reason or "")

    # Frontend's late success must not resurrect anything.
    res = env.engine.complete_step(run.id, "frontend", fe_attempt, StepOutcome(status="succeeded"), _agent())
    assert res.value == "stale"
    assert env.engine.get_run(run.id).step("frontend").status == "cancelled"
    assert not env.executor.calls_for("pull_request")


def test_upstream_artifacts_are_passed_to_dependents(env: Env) -> None:
    run = env.start(Profile.FAST)
    triage_artifacts = [a.name for a in env.executor.calls_for("develop")[0].upstream_artifacts]
    assert triage_artifacts == ["triage-result"]
    assert run.step("develop").status == "succeeded" or run.step("pull_request").status != "pending"


def test_ledger_records_audit_events(env: Env) -> None:
    run = env.start(Profile.FAST)
    types = [e.type for e in env.engine.events(run.id)]
    assert types[0] == "run.created"
    assert "step.started" in types and "step.succeeded" in types


def _agent():  # noqa: ANN202
    from tests.conftest import AGENT

    return AGENT
