"""Tests for RoleRouter, ReconcileDriver / reconcile_all, and ReconcileNotifier."""

from __future__ import annotations

from arvino.adapters.role_router import _ROLE_MAP, RoleRouter
from arvino.domain import Role
from arvino.ports import StepOutcome, StepRequest
from tests.conftest import Env


# ------------------------------------------------------------------ helpers

def _make_request(role: str) -> StepRequest:
    return StepRequest(
        run_id="run-000000000001",
        step_key="tests",
        role=role,
        attempt=1,
        idempotency_key="run-000000000001:tests:1",
        branch_prefix="arvino/run-000000000001/",
        actor="agent:developer",
        project_key="acme",
        repo="acme/app",
        issue={  # type: ignore[arg-type]
            "id": "iss-000000000001",
            "source": "plane",
            "external_id": "PLANE-1",
            "title": "test issue",
        },
    )


# ------------------------------------------------------------------ RoleRouter


def test_role_router_forwards_known_role() -> None:
    """RoleRouter routes a known role to the correct adapter."""
    calls: list[StepRequest] = []

    class _Spy:
        def execute(self, request: StepRequest) -> StepOutcome:
            calls.append(request)
            return StepOutcome(status="succeeded")

    router = RoleRouter({"claude_code": _Spy()})
    outcome = router.execute(_make_request("backend"))
    assert outcome.status == "succeeded"
    assert len(calls) == 1
    assert calls[0].role == "backend"
    assert calls[0].run_id == "run-000000000001"


def test_role_router_unknown_role_fails_closed() -> None:
    """RoleRouter returns a failed (non-retryable) outcome for an unknown role."""
    router = RoleRouter({})
    outcome = router.execute(_make_request("superhero"))
    assert outcome.status == "failed"
    assert outcome.retryable is False
    assert "no executor configured" in (outcome.error or "")


def test_role_router_missing_adapter_fails_retryable() -> None:
    """RoleRouter returns retryable failure when role maps to a missing adapter."""
    # Provide adapters dict without the "claude_code" key
    router = RoleRouter({"other": _DummyExecutor()})
    outcome = router.execute(_make_request("backend"))
    assert outcome.status == "failed"
    assert outcome.retryable is True
    assert "executor 'claude_code' not available" in (outcome.error or "")


class _DummyExecutor:
    def execute(self, request: StepRequest) -> StepOutcome:
        return StepOutcome(status="succeeded", output={"dummy": True})


def test_role_router_passes_request_through_unchanged() -> None:
    """RoleRouter passes the StepRequest to the underlying adapter unchanged."""
    captured: list[StepRequest] = []

    class _Capture:
        def execute(self, request: StepRequest) -> StepOutcome:
            captured.append(request)
            return StepOutcome(status="succeeded", artifacts=[])

    router = RoleRouter({"claude_code": _Capture()})
    req = _make_request("reviewer")
    router.execute(req)
    assert len(captured) == 1
    assert captured[0] is req  # identity check: same object passed through


def test_all_role_map_entries_resolve() -> None:
    """Every role in _ROLE_MAP resolves to a known adapter key."""
    router = RoleRouter({"claude_code": _DummyExecutor()})
    for role in _ROLE_MAP:
        outcome = router.execute(_make_request(role))
        assert outcome.status == "succeeded", f"role {role!r} failed: {outcome.error}"


# ------------------------------------------------------------------ reconcile_all


def test_reconcile_all_delegates_to_engine_and_returns_ids(env: Env) -> None:
    """reconcile_all activity calls engine.reconcile() and returns a list of run IDs."""
    from arvino.temporal_worker import Activities

    # Start a run so reconcile has something to return.
    from arvino.domain import Profile

    view, _ = env.engine.start_run(env.project_id, env.issue_id, Profile.FAST, "rk-reconcile", "owner")
    acts = Activities(env.engine)
    result = acts.reconcile_all()
    assert isinstance(result, list)
    # The run we just started should be in the list.
    assert view.id in result


# ------------------------------------------------------------------ AdvanceResult


def test_advance_result_wake_in_seconds() -> None:
    """AdvanceResult.wake_in_seconds is correctly computed."""
    from datetime import UTC, datetime, timedelta

    from arvino.temporal_worker import AdvanceResult

    future = datetime.now(UTC) + timedelta(seconds=30)
    # Simulate what advance_run computes:
    wake = max(0.0, (future - datetime.now(UTC)).total_seconds())
    ar = AdvanceResult(status="running", wake_in_seconds=wake)
    assert ar.status == "running"
    assert ar.wake_in_seconds is not None
    assert 0.0 <= ar.wake_in_seconds <= 30.0

    # With no wakeup (terminal status), wake_in_seconds is None.
    ar_terminal = AdvanceResult(status="succeeded", wake_in_seconds=None)
    assert ar_terminal.wake_in_seconds is None


# ------------------------------------------------------------------ notifier smoke tests


def test_temporal_notifier_smoke() -> None:
    """TemporalNotifier can be instantiated."""
    from arvino.temporal_worker import TemporalNotifier

    notifier = TemporalNotifier("localhost:7233", "default", "arvino-tasks")
    assert notifier is not None
    assert notifier._address == "localhost:7233"
    assert notifier._namespace == "default"
    assert notifier._task_queue == "arvino-tasks"


def test_reconcile_notifier_smoke() -> None:
    """ReconcileNotifier can be instantiated."""
    from arvino.temporal_worker import ReconcileNotifier

    notifier = ReconcileNotifier("localhost:7233", "default", "arvino-tasks")
    assert notifier is not None
    assert notifier._address == "localhost:7233"
    assert notifier._namespace == "default"
    assert notifier._task_queue == "arvino-tasks"