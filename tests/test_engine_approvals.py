"""Approval blocking and separation of duties."""

from __future__ import annotations

import pytest

from arvino.db import ApprovalRow, transaction
from arvino.domain import ConflictError, PolicyDeniedError, Principal, PrincipalKind, Profile
from arvino.fakes import StaticPolicy
from arvino.ports import StepOutcome
from tests.conftest import AGENT, ALL_CHECKS, OWNER, SHA, Env, build_env


def to_release_approval(env: Env) -> str:
    env.executor.script("pull_request", env.pr_script())
    run = env.start(Profile.STANDARD)
    env.webhook_pr(run.id)
    env.checks(run.id, ALL_CHECKS)
    env.converge("acme-preview")
    run = env.engine.get_run(run.id)
    assert run.step("release_approval").status == "waiting_approval", run.statuses()
    return run.id


def test_unapproved_release_blocks_downstream_and_never_promotes(env: Env) -> None:
    run_id = to_release_approval(env)
    run = env.engine.get_run(run_id)
    assert run.status == "waiting_approval"
    assert run.step("release").status == "pending"
    assert [r.environment for r in env.promoter.requests] == [
        "preview"
    ]  # production never requested
    for _ in range(3):
        env.engine.advance(run_id)
    assert [r.environment for r in env.promoter.requests] == ["preview"]
    assert [a.status for a in run.approvals] == ["pending"]


def test_owner_approval_unblocks_release(env: Env) -> None:
    run_id = to_release_approval(env)
    # PR gets merged by the human owner: this gives the production revision.
    env.engine.update_git_state(
        run_id, merged=True, merge_sha="b" * 40, merged_by="owner", source="test"
    )
    appr = env.approve(run_id, "release_approval")
    assert appr.status == "approved" and appr.decided_by and appr.decided_by["id"] == "owner"
    run = env.engine.get_run(run_id)
    assert run.step("release").status == "running"
    assert env.promoter.requests[-1].environment == "production"
    assert env.promoter.requests[-1].revision == "b" * 40
    assert "production_approval" not in run.statuses()
    env.converge("acme-prod", revision="b" * 40)
    assert env.engine.get_run(run_id).status == "succeeded"


def test_agent_cannot_approve(env: Env) -> None:
    run_id = to_release_approval(env)
    with pytest.raises(PolicyDeniedError, match="human"):
        env.approve(run_id, "release_approval", AGENT)
    assert env.engine.get_run(run_id).step("release_approval").status == "waiting_approval"
    assert "approval.denied" in [e.type for e in env.engine.events(run_id)]


def test_non_owner_human_cannot_approve(env: Env) -> None:
    run_id = to_release_approval(env)
    other = Principal(id="contractor", kind=PrincipalKind.HUMAN, roles=("qa",))
    with pytest.raises(PolicyDeniedError, match="owner"):
        env.approve(run_id, "release_approval", other)


def test_actor_on_the_run_cannot_approve_it(env: Env) -> None:
    run_id = to_release_approval(env)
    # A human principal whose id matches an actor that executed steps in this run.
    impersonator = Principal(id="agent:developer", kind=PrincipalKind.HUMAN, roles=("owner",))
    with pytest.raises(PolicyDeniedError, match="separation of duties"):
        env.approve(run_id, "release_approval", impersonator)


def test_policy_denial_blocks_even_a_valid_owner() -> None:
    env = build_env(policy=StaticPolicy(allow=True))
    run_id = to_release_approval(env)
    env.policy.allow, env.policy.reasons = False, ["freeze window"]
    with pytest.raises(PolicyDeniedError, match="freeze window"):
        env.approve(run_id, "release_approval")
    assert env.engine.get_run(run_id).step("release").status == "pending"


def test_approval_is_idempotent_and_conflicts_are_rejected(env: Env) -> None:
    run_id = to_release_approval(env)
    first = env.approve(run_id, "release_approval")
    again = env.engine.decide_approval(first.id, OWNER, "approved")
    assert again.status == "approved" and again.decided_at == first.decided_at
    with pytest.raises(ConflictError):
        env.engine.decide_approval(first.id, OWNER, "rejected")


def test_rejection_fails_run_and_cancels_remaining(env: Env) -> None:
    run_id = to_release_approval(env)
    appr = env.engine.get_run(run_id).approvals[0]
    env.engine.decide_approval(appr.id, OWNER, "rejected", "not now")
    run = env.engine.get_run(run_id)
    assert run.status == "failed" and run.step("release").status == "cancelled"
    assert run.approvals[0].status == "rejected" and run.approvals[0].reason == "not now"
    assert len(env.promoter.requests) == 1  # preview only


def test_approval_expires_and_fails_run(env: Env) -> None:
    run_id = to_release_approval(env)
    env.clock.advance(env.engine.get_run(run_id).step("release_approval").timeout_seconds + 1)
    assert (run_id, "release_approval") in env.engine.reap_timeouts()
    run = env.engine.get_run(run_id)
    assert run.status == "failed" and run.approvals[0].status == "expired"
    with pytest.raises(ConflictError):
        env.engine.decide_approval(run.approvals[0].id, OWNER, "approved")


def test_approval_request_is_idempotent_across_advances(env: Env) -> None:
    run_id = to_release_approval(env)
    for _ in range(4):
        env.engine.advance(run_id)
    assert len(env.engine.get_run(run_id).approvals) == 1


# ---- structural invariants hold even if policy is wide open and the ledger is tampered with


def test_production_never_dispatches_without_human_owner_approval_row() -> None:
    env = build_env(policy=StaticPolicy(allow=True))
    run_id = to_release_approval(env)
    appr = env.engine.get_run(run_id).approvals[0]
    # Tamper: mark the approval approved but "decided by" an agent. Policy allows everything.
    with transaction(env.engine._sf) as s:
        row = s.get(ApprovalRow, appr.id)
        assert row is not None
        row.status = "approved"
        row.decided_by = AGENT.model_dump(mode="json")
    # Force the approval step to succeed as if the engine had been fooled.
    from sqlalchemy import update

    from arvino.db import StepRow

    with transaction(env.engine._sf) as s:
        s.execute(
            update(StepRow)
            .where(StepRow.run_id == run_id, StepRow.key == "release_approval")
            .values(status="succeeded")
        )
    run = env.engine.advance(run_id)
    assert run.step("release").status == "failed"
    assert "no approved production-deploy approval" in (run.step("release").last_error or "")
    assert all(r.environment != "production" for r in env.promoter.requests)


def test_governed_implementation_waits_for_architecture_approval(env: Env) -> None:
    env.executor.script("adr", StepOutcome(status="succeeded"))
    run = env.start(Profile.GOVERNED)
    assert run.status == "waiting_approval"
    assert run.step("architecture_approval").status == "waiting_approval"
    assert run.step("implementation").status == "pending"
    assert not env.executor.calls_for("implementation")
    env.approve(run.id, "architecture_approval")
    assert env.executor.calls_for("implementation")


def test_governed_requires_staging_then_second_approval_then_production(env: Env) -> None:
    env.executor.script("pull_request", env.pr_script())
    run = env.start(Profile.GOVERNED)
    env.approve(run.id, "architecture_approval")
    env.webhook_pr(run.id)
    env.checks(run.id, ALL_CHECKS)
    run = env.engine.get_run(run.id)
    assert run.step("staging").status == "running"
    assert run.step("production_approval").status == "pending"  # not before staging converges
    env.engine.update_git_state(
        run.id, merged=True, merge_sha=SHA, merged_by="owner", source="test"
    )
    env.converge("acme-staging")
    run = env.engine.get_run(run.id)
    assert run.step("production_approval").status == "waiting_approval"
    assert run.step("production").status == "pending"
    env.approve(run.id, "production_approval")
    run = env.engine.get_run(run.id)
    assert run.step("production").status == "running"
    env.converge("acme-prod")
    run = env.engine.get_run(run.id)
    assert run.step("post_deploy_verification").status == "succeeded"
    assert run.status == "succeeded"
    # Two distinct owner approvals were recorded.
    assert sorted(a.kind for a in run.approvals) == ["architecture", "production-deploy"]
