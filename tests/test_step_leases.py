"""External-executor leases: A1-A4 offline acceptance (design §6).

A1 is exercised as a sequential duplicate claim: SQLite cannot reliably model
two simultaneous writers, so the CAS predicate (status/attempt/hash unchanged)
is the guarantee under test.  A2 covers expiry isolation and stop-evidence
reclaim; A3 covers token/principal/attempt/expiry fencing; A4 covers the
per-repository write mutex and read-only parallelism.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from arvino.api import create_app
from arvino.db import RepoWriteLockRow, transaction
from arvino.domain import ConflictError, PolicyDeniedError, Principal, PrincipalKind, Profile
from arvino.engine import CompletionResult
from arvino.ports import StepOutcome
from tests import payloads as P
from tests.conftest import AGENT, OWNER, REPO, Env

DEV = Principal(id="agent:developer", kind=PrincipalKind.AGENT, roles=("developer",))
DEV2 = Principal(id="agent:developer-2", kind=PrincipalKind.AGENT, roles=("developer",))
READER = Principal(id="agent:qa", kind=PrincipalKind.AGENT, roles=("qa",))
PRODUCT = Principal(id="agent:product", kind=PrincipalKind.AGENT, roles=("product",))
PRODUCT2 = Principal(id="agent:product-2", kind=PrincipalKind.AGENT, roles=("product",))


def running_fast(env: Env, key: str = "k1") -> str:
    env.executor.script("develop", StepOutcome(status="running"))
    run = env.start(Profile.FAST, key)
    assert run.step("develop").status == "running", run.statuses()
    return run.id


def repo_lock(env: Env) -> tuple[str, str, str, object] | None:
    with transaction(env.engine._sf) as s:
        row = s.get(RepoWriteLockRow, REPO.lower())
        if row is None:
            return None
        return (row.run_id, row.step_key, row.executor_principal, row.expires_at)


# ----------------------------------------------------------------------------- A1


def test_a1_duplicate_claim_only_one_wins(env: Env) -> None:
    run_id = running_fast(env)
    lease = env.engine.claim_step(run_id, "develop", DEV, ttl_seconds=60)
    assert lease.lease_token
    assert lease.repo_key == REPO
    assert lease.role_verified is True

    with pytest.raises(ConflictError, match="already leased"):
        env.engine.claim_step(run_id, "develop", DEV2, ttl_seconds=60)
    with pytest.raises(ConflictError, match="already leased"):
        env.engine.claim_step(run_id, "develop", DEV, ttl_seconds=60)

    assert [e.type for e in env.engine.events(run_id)].count("step.lease_claimed") == 1


def test_claim_requires_the_step_role(env: Env) -> None:
    run_id = running_fast(env)
    with pytest.raises(PolicyDeniedError, match="lacks role"):
        env.engine.claim_step(run_id, "develop", READER, ttl_seconds=60)
    # Legacy static agent tokens carry no role claims: accepted, but flagged.
    lease = env.engine.claim_step(run_id, "develop", AGENT, ttl_seconds=60)
    assert lease.role_verified is False


# ----------------------------------------------------------------------------- A2


def test_a2_expired_lease_requires_reclaim_with_stop_evidence(env: Env) -> None:
    run_id = running_fast(env)
    lease = env.engine.claim_step(run_id, "develop", DEV, ttl_seconds=60)
    calls_before = len(env.executor.calls_for("develop"))

    # Lease expiry is not stop evidence, and reclaim is operator-only.
    with pytest.raises(PolicyDeniedError, match="stop evidence"):
        env.engine.reclaim_lease(
            run_id, "develop", OWNER, stop_evidence="lease_expired", detail="ttl elapsed"
        )
    with pytest.raises(PolicyDeniedError, match="operator principal"):
        env.engine.reclaim_lease(
            run_id, "develop", DEV, stop_evidence="operator_declared", detail="trust me"
        )
    # Even with evidence, a still-valid lease cannot be reclaimed.
    with pytest.raises(ConflictError, match="still valid"):
        env.engine.reclaim_lease(
            run_id, "develop", OWNER, stop_evidence="operator_declared", detail="too early"
        )

    env.clock.advance(61)
    assert env.engine.list_work() == []
    timeout = env.engine.get_run(run_id).step("develop").timeout_seconds
    env.clock.advance(timeout + 1)
    assert env.engine.reap_timeouts() == []
    run = env.engine.get_run(run_id)
    assert run.step("develop").status == "running"
    assert len(env.executor.calls_for("develop")) == calls_before
    assert repo_lock(env) is not None

    released = env.engine.reclaim_lease(
        run_id,
        "develop",
        OWNER,
        stop_evidence="operator_declared",
        detail="session terminated; evidence OPS-1",
    )
    assert released.attempt == lease.attempt
    assert released.executor_principal == DEV.id
    run = env.engine.get_run(run_id)
    assert run.step("develop").status == "pending"
    assert run.step("develop").executor_lease_expires_at is None
    assert repo_lock(env) is None

    env.engine.advance(run_id)
    run = env.engine.get_run(run_id)
    assert run.step("develop").status == "running"
    assert run.step("develop").attempt == lease.attempt + 1
    assert [w.step_key for w in env.engine.list_work()] == ["develop"]


# ----------------------------------------------------------------------------- A3


def test_a3_completion_requires_live_token_principal_and_attempt(env: Env) -> None:
    run_id = running_fast(env)
    lease = env.engine.claim_step(run_id, "develop", DEV, ttl_seconds=60)
    attempt = env.engine.get_run(run_id).step("develop").attempt
    ok = StepOutcome(status="succeeded")

    assert (
        env.engine.complete_step(run_id, "develop", attempt, ok, DEV)
        is CompletionResult.STALE
    )
    assert (
        env.engine.complete_step(
            run_id, "develop", attempt, ok, DEV2, lease_token=lease.lease_token
        )
        is CompletionResult.STALE
    )
    assert (
        env.engine.complete_step(
            run_id, "develop", attempt, ok, DEV, lease_token="not-the-token"
        )
        is CompletionResult.STALE
    )
    assert (
        env.engine.complete_step(
            run_id, "develop", attempt - 1, ok, DEV, lease_token=lease.lease_token
        )
        is CompletionResult.STALE
    )
    run = env.engine.get_run(run_id)
    assert run.step("develop").status == "running"
    assert repo_lock(env) is not None

    events = {e.type for e in env.engine.events(run_id)}
    assert {
        "step.lease_token_missing",
        "step.lease_principal_mismatch",
        "step.lease_token_mismatch",
        "step.stale_completion_ignored",
    } <= events

    env.clock.advance(61)
    assert (
        env.engine.complete_step(
            run_id, "develop", attempt, ok, DEV, lease_token=lease.lease_token
        )
        is CompletionResult.EXPIRED
    )
    assert env.engine.get_run(run_id).step("develop").status == "running"
    assert "step.lease_expired_completion_ignored" in {
        e.type for e in env.engine.events(run_id)
    }


def test_a3_valid_completion_consumes_lease_and_repo_lock(env: Env) -> None:
    run_id = running_fast(env)
    lease = env.engine.claim_step(run_id, "develop", DEV, ttl_seconds=60)
    attempt = env.engine.get_run(run_id).step("develop").attempt

    result = env.engine.complete_step(
        run_id,
        "develop",
        attempt,
        StepOutcome(status="succeeded"),
        DEV,
        lease_token=lease.lease_token,
    )
    assert result is CompletionResult.APPLIED

    run = env.engine.get_run(run_id)
    assert run.step("develop").status == "succeeded"
    assert run.step("develop").executor_lease_expires_at is None
    assert run.step("develop").executor_principal == DEV.id
    assert repo_lock(env) is None


def test_renew_extends_lease_and_release_returns_to_pending(env: Env) -> None:
    run_id = running_fast(env)
    lease = env.engine.claim_step(run_id, "develop", DEV, ttl_seconds=60)
    env.clock.advance(30)

    renewed = env.engine.renew_lease(
        run_id, "develop", lease.lease_token, DEV, ttl_seconds=120
    )
    assert renewed.lease_expires_at is not None
    assert renewed.lease_expires_at > lease.lease_expires_at  # type: ignore[operator]
    assert renewed.lease_token is None

    with pytest.raises(ConflictError, match="token mismatch"):
        env.engine.renew_lease(run_id, "develop", "wrong", DEV)
    with pytest.raises(ConflictError, match="principal mismatch"):
        env.engine.renew_lease(run_id, "develop", lease.lease_token, DEV2)

    released = env.engine.release_lease(run_id, "develop", lease.lease_token, DEV)
    assert released.lease_expires_at is None
    assert env.engine.get_run(run_id).step("develop").status == "pending"
    assert repo_lock(env) is None


# ----------------------------------------------------------------------------- A4


def test_a4_same_repo_write_mutex_across_runs(env: Env) -> None:
    run1 = running_fast(env, "k1")
    lease1 = env.engine.claim_step(run1, "develop", DEV, ttl_seconds=60)
    assert lease1.repo_key == REPO
    assert repo_lock(env)[0] == run1  # type: ignore[index]

    run2 = env.start(Profile.FAST, "k2")
    assert run2.step("develop").status == "running"
    with pytest.raises(ConflictError, match="write-locked") as blocked:
        env.engine.claim_step(run2.id, "develop", DEV2, ttl_seconds=60)
    assert DEV.id in str(blocked.value)
    assert run1 in str(blocked.value)

    env.engine.release_lease(run1, "develop", lease1.lease_token, DEV)
    lease2 = env.engine.claim_step(run2.id, "develop", DEV2, ttl_seconds=60)
    assert lease2.repo_key == REPO
    assert repo_lock(env)[0] == run2.id  # type: ignore[index]


def test_a4_read_only_steps_do_not_take_the_repo_lock(env: Env) -> None:
    env.executor.script("spec", StepOutcome(status="running"))
    run1 = env.start(Profile.STANDARD, "s1")
    run2 = env.start(Profile.STANDARD, "s2")
    assert run1.step("spec").status == run2.step("spec").status == "running"

    lease1 = env.engine.claim_step(run1.id, "spec", PRODUCT, ttl_seconds=60)
    lease2 = env.engine.claim_step(run2.id, "spec", PRODUCT2, ttl_seconds=60)
    assert lease1.repo_key == lease2.repo_key == REPO
    assert repo_lock(env) is None


# ----------------------------------------------------------------------------- HTTP contract


def test_api_claim_complete_and_reclaim_contract(env: Env) -> None:
    from tests.test_api import make_settings

    client = TestClient(create_app(env.engine, make_settings()))
    run_id = running_fast(env)
    agent = {"Authorization": f"Bearer {P.AGENT_TOKEN}"}
    owner = {"Authorization": f"Bearer {P.OWNER_TOKEN}"}

    claim = client.post(
        f"/runs/{run_id}/steps/develop/claim", headers=agent, json={"ttl_seconds": 60}
    )
    assert claim.status_code == 200, claim.text
    lease = claim.json()
    assert lease["lease_token"]
    assert lease["repo_key"] == REPO
    assert client.get("/work", headers=agent).json() == []

    again = client.post(
        f"/runs/{run_id}/steps/develop/claim", headers=agent, json={"ttl_seconds": 60}
    )
    assert again.status_code == 409

    attempt = env.engine.get_run(run_id).step("develop").attempt
    missing = client.post(
        f"/runs/{run_id}/steps/develop/complete",
        headers=agent,
        json={"attempt": attempt, "status": "succeeded"},
    )
    assert missing.json()["result"] == "stale"

    applied = client.post(
        f"/runs/{run_id}/steps/develop/complete",
        headers=agent,
        json={
            "attempt": attempt,
            "status": "succeeded",
            "lease_token": lease["lease_token"],
        },
    )
    assert applied.json()["result"] == "applied"

    denied = client.post(
        f"/runs/{run_id}/steps/develop/reclaim",
        headers=agent,
        json={"stop_evidence": "operator_declared", "detail": "not an operator"},
    )
    assert denied.status_code == 403

    # Owner reclaim requires expiry plus accepted evidence.
    run_id = running_fast(env, "k2")
    claim = client.post(
        f"/runs/{run_id}/steps/develop/claim", headers=agent, json={"ttl_seconds": 60}
    )
    token = claim.json()["lease_token"]
    early = client.post(
        f"/runs/{run_id}/steps/develop/reclaim",
        headers=owner,
        json={"stop_evidence": "operator_declared", "detail": "too early"},
    )
    assert early.status_code == 409

    env.clock.advance(61)
    bad_evidence = client.post(
        f"/runs/{run_id}/steps/develop/reclaim",
        headers=owner,
        json={"stop_evidence": "lease_expired", "detail": "ttl elapsed"},
    )
    assert bad_evidence.status_code == 422

    reclaimed = client.post(
        f"/runs/{run_id}/steps/develop/reclaim",
        headers=owner,
        json={"stop_evidence": "operator_declared", "detail": "session gone (OPS-2)"},
    )
    assert reclaimed.status_code == 200, reclaimed.text
    assert reclaimed.json()["lease_expires_at"] is None
    assert env.engine.get_run(run_id).step("develop").status == "pending"

    late = client.post(
        f"/runs/{run_id}/steps/develop/complete",
        headers=agent,
        json={"attempt": attempt, "status": "succeeded", "lease_token": token},
    )
    assert late.json()["result"] == "stale"


# ----------------------------------------------------------------------------- migration drill


def test_migration_round_trip(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from arvino.db import make_engine

    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    engine = make_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    cfg = Config(str(ini))
    with engine.connect() as conn:
        cfg.attributes["connection"] = conn

        command.upgrade(cfg, "head")
        columns = {c["name"] for c in inspect(conn).get_columns("steps")}
        assert "executor_principal" in columns
        assert "repo_write_locks" in inspect(conn).get_table_names()

        command.downgrade(cfg, "b1c2d3e4f5a6")
        columns = {c["name"] for c in inspect(conn).get_columns("steps")}
        assert "executor_principal" not in columns
        assert "repo_write_locks" not in inspect(conn).get_table_names()

        command.upgrade(cfg, "head")
        assert "repo_write_locks" in inspect(conn).get_table_names()
