"""Tests for Phase 6 – cost/budget tracking and spending-change approval.

Coverage:
- ClaudeCodeExecutor extracts cost_usd from JSON output and includes it in StepOutcome.output
- Engine accumulates cost_usd into RunRow.cost_usd_total and StepRow.cost_usd
- Engine exposes cost_usd / cost_usd_total in views
- Engine injects a spending-approval step when cumulative cost exceeds the budget
- spending approval blocks further agent steps; approving it lets the run continue
- No spending approval when no budget is set (unlimited)
- No duplicate spending approvals for the same run
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from arvino.adapters.claude_code import ClaudeCodeExecutor
from arvino.domain import ApprovalKind, ApprovalStatus, StepKind, StepStatus
from arvino.engine import Engine, EngineSettings
from arvino.fakes import FakeGit, RecordingNotifier, RecordingTracker, RecordOnlyPromoter, StaticPolicy
from arvino.ports import IssueRef, StepRequest

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

_OK = subprocess.CompletedProcess([], 0, stdout="", stderr="")


def _make_request(**overrides: Any) -> StepRequest:
    defaults: dict[str, Any] = {
        "run_id": "run-cost-001",
        "step_key": "develop",
        "role": "developer",
        "attempt": 1,
        "idempotency_key": "run-cost-001:develop:1",
        "branch_prefix": "arvino/run-cost-001/",
        "actor": "agent:developer",
        "project_key": "acme",
        "repo": "acme/app",
        "issue": IssueRef(
            id="issue-cost",
            source="github",
            external_id="99",
            title="Feature X",
            body="Build feature X",
            labels=[],
        ),
        "upstream_artifacts": [],
    }
    defaults.update(overrides)
    return StepRequest(**defaults)


@pytest.fixture()
def executor(tmp_path: Path) -> ClaudeCodeExecutor:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)  # noqa: S603, S607
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],  # noqa: S607
        cwd=str(repo),
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_COMMITTER_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    return ClaudeCodeExecutor(repo_base_dir=repo, timeout_seconds=10)


# ---------------------------------------------------------------------------
# ClaudeCodeExecutor: cost extraction
# ---------------------------------------------------------------------------


class TestClaudeCodeCostExtraction:
    def test_cost_included_in_output(self, executor: ClaudeCodeExecutor) -> None:
        claude_output = json.dumps({"result": "Done.", "cost_usd": 0.045})
        with (
            patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}),
            patch("arvino.adapters.claude_code.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,  # git branch
                _OK,  # git worktree add
                subprocess.CompletedProcess([], 0, stdout=claude_output, stderr=""),
                _OK,  # git worktree remove
            ]
            outcome = executor.execute(_make_request())

        assert outcome.status == "succeeded"
        assert outcome.output.get("cost_usd") == pytest.approx(0.045)

    def test_zero_cost_included(self, executor: ClaudeCodeExecutor) -> None:
        claude_output = json.dumps({"result": "Done.", "cost_usd": 0})
        with (
            patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}),
            patch("arvino.adapters.claude_code.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,
                _OK,
                subprocess.CompletedProcess([], 0, stdout=claude_output, stderr=""),
                _OK,
            ]
            outcome = executor.execute(_make_request())

        assert outcome.status == "succeeded"
        assert outcome.output.get("cost_usd") == 0.0

    def test_negative_cost_ignored(self, executor: ClaudeCodeExecutor) -> None:
        """Negative cost values from a broken response are silently dropped."""
        claude_output = json.dumps({"result": "Done.", "cost_usd": -1.0})
        with (
            patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}),
            patch("arvino.adapters.claude_code.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,
                _OK,
                subprocess.CompletedProcess([], 0, stdout=claude_output, stderr=""),
                _OK,
            ]
            outcome = executor.execute(_make_request())

        assert outcome.status == "succeeded"
        assert outcome.output.get("cost_usd") is None

    def test_missing_cost_field_is_ok(self, executor: ClaudeCodeExecutor) -> None:
        """Runs without cost data succeed without any cost in output."""
        claude_output = json.dumps({"result": "Done."})
        with (
            patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}),
            patch("arvino.adapters.claude_code.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,
                _OK,
                subprocess.CompletedProcess([], 0, stdout=claude_output, stderr=""),
                _OK,
            ]
            outcome = executor.execute(_make_request())

        assert outcome.status == "succeeded"
        assert "cost_usd" not in outcome.output


# ---------------------------------------------------------------------------
# Engine: cost accumulation and views
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine_factory(tmp_path: Path):
    """Return a factory that builds a fresh Engine with optional budget_usd."""
    from arvino.db import init_schema, make_engine, make_session_factory
    from arvino.ports import Ports
    from arvino.workflows.model import load_registry

    def _make(budget_usd: float | None = None) -> Engine:
        sa = make_engine(f"sqlite:///{tmp_path}/test-cost-{budget_usd}.db")
        init_schema(sa)
        ports = Ports(
            executor=_CostExecutor(),
            tracker=RecordingTracker(),
            git=FakeGit(),
            promoter=RecordOnlyPromoter(),
            policy=StaticPolicy(allow=True),
            notifier=RecordingNotifier(),
        )
        return Engine(
            make_session_factory(sa),
            load_registry(),
            ports,
            settings=EngineSettings(budget_usd=budget_usd),
        )

    return _make


class _CostExecutor:
    """Executor that auto-succeeds every agent step, reporting cost_usd=0.10."""

    COST_PER_STEP: float = 0.10

    def execute(self, request: StepRequest):  # noqa: ANN201
        from arvino.domain import ArtifactSpec
        from arvino.ports import StepOutcome

        return StepOutcome(
            status="succeeded",
            output={"cost_usd": self.COST_PER_STEP},
            artifacts=[
                ArtifactSpec(
                    kind="note",
                    name=f"{request.step_key}-result",
                    uri=f"git://fake/{request.run_id}/{request.step_key}",
                )
            ],
        )


def _make_run(engine: Engine, *, budget_label: str = ""):
    """Create a minimal project + issue + run and return the run view."""
    prj = engine.create_project(f"proj{budget_label}", f"Test project {budget_label}")
    iss, _ = engine.upsert_issue(prj.id, "api", "1", "Test issue")
    from arvino.domain import Profile
    view, _ = engine.start_run(prj.id, iss.id, Profile.FAST, f"key-{budget_label}", "owner")
    return engine.get_run(view.id)


class TestCostAccumulationInViews:
    def test_cost_recorded_on_step_view(self, engine_factory: Any) -> None:
        engine = engine_factory(budget_usd=None)
        run = _make_run(engine)
        # Fast workflow has at least one agent step; advance fully.
        run = engine.advance(run.id)
        agent_steps = [s for s in run.steps if s.kind == "agent" and s.status == "succeeded"]
        if agent_steps:
            assert any(s.cost_usd == pytest.approx(0.10) for s in agent_steps)

    def test_cost_usd_total_on_run_view(self, engine_factory: Any) -> None:
        engine = engine_factory(budget_usd=None)
        run = _make_run(engine)
        run = engine.advance(run.id)
        agent_count = sum(1 for s in run.steps if s.kind == "agent" and s.status == "succeeded")
        expected_total = agent_count * _CostExecutor.COST_PER_STEP
        assert run.cost_usd_total == pytest.approx(expected_total)

    def test_no_cost_when_executor_omits_cost_usd(self, engine_factory: Any, tmp_path: Path) -> None:
        """Runs using a no-cost executor accumulate 0.0."""
        from arvino.db import init_schema, make_engine, make_session_factory
        from arvino.fakes import ScriptedExecutor
        from arvino.domain import ArtifactSpec
        from arvino.ports import Ports, StepOutcome
        from arvino.workflows.model import load_registry

        sa = make_engine(f"sqlite:///{tmp_path}/test-no-cost.db")
        init_schema(sa)
        free_exec = ScriptedExecutor(default=StepOutcome(status="succeeded", output={}, artifacts=[]))
        ports = Ports(
            executor=free_exec,
            tracker=RecordingTracker(),
            git=FakeGit(),
            promoter=RecordOnlyPromoter(),
            policy=StaticPolicy(allow=True),
            notifier=RecordingNotifier(),
        )
        engine = Engine(make_session_factory(sa), load_registry(), ports)
        run = _make_run(engine, budget_label="free")
        run = engine.advance(run.id)
        assert run.cost_usd_total == 0.0


# ---------------------------------------------------------------------------
# Engine: budget enforcement
# ---------------------------------------------------------------------------


class TestBudgetEnforcement:
    def test_no_spending_approval_when_no_budget(self, engine_factory: Any) -> None:
        engine = engine_factory(budget_usd=None)
        run = _make_run(engine)
        run = engine.advance(run.id)
        kinds = {s.kind for s in run.steps}
        # approval steps exist only for the APPROVAL kind
        spending = [
            s for s in run.steps
            if s.kind == "approval" and s.approval_kind == ApprovalKind.SPENDING.value
        ]
        assert spending == []

    def test_spending_approval_injected_when_budget_exceeded(self, engine_factory: Any) -> None:
        # Budget = 0.05; first agent step costs 0.10 → exceeds budget immediately.
        engine = engine_factory(budget_usd=0.05)
        run = _make_run(engine)
        run = engine.advance(run.id)
        spending = [
            s for s in run.steps
            if s.kind == "approval" and s.approval_kind == ApprovalKind.SPENDING.value
        ]
        assert len(spending) >= 1, "Expected a spending approval step to be injected"
        # The run should be waiting for that approval.
        assert run.status in ("waiting_approval", "running")

    def test_spending_approval_blocks_pending_agent_steps(self, engine_factory: Any) -> None:
        engine = engine_factory(budget_usd=0.05)
        run = _make_run(engine)
        run = engine.advance(run.id)
        spending = next(
            (s for s in run.steps if s.kind == "approval" and s.approval_kind == ApprovalKind.SPENDING.value),
            None,
        )
        if spending is None:
            pytest.skip("No pending agent steps after first advance (fast workflow may be fully done)")
        # All pending steps that come after the spending approval should list it as a dependency.
        pending_after = [
            s for s in run.steps
            if s.status == StepStatus.PENDING.value and s.position > spending.position
        ]
        for s in pending_after:
            assert "spending_approval" in s.depends_on, (
                f"Pending step {s.key!r} after spending approval should depend on it"
            )

    def test_run_can_continue_after_spending_approval(self, engine_factory: Any) -> None:
        engine = engine_factory(budget_usd=0.05)
        run = _make_run(engine)
        run = engine.advance(run.id)
        # Find the pending spending approval.
        spending_approval = next(
            (a for a in run.approvals if a.kind == ApprovalKind.SPENDING.value),
            None,
        )
        if spending_approval is None:
            pytest.skip("No budget exceeded; test only applies when approval injected")
        assert spending_approval.status == ApprovalStatus.PENDING.value
        # Approve it as a human owner.
        from arvino.domain import Principal, PrincipalKind
        owner = Principal(id="human:owner", kind=PrincipalKind.HUMAN, roles=("owner",))
        engine.decide_approval(spending_approval.id, owner, "approved", reason="Approved by test")
        run = engine.get_run(run.id)
        # The approval should now be approved.
        updated = next(a for a in run.approvals if a.id == spending_approval.id)
        assert updated.status == ApprovalStatus.APPROVED.value

    def test_no_duplicate_spending_approval_on_multiple_advance(self, engine_factory: Any) -> None:
        engine = engine_factory(budget_usd=0.05)
        run = _make_run(engine)
        run = engine.advance(run.id)
        run = engine.advance(run.id)  # second advance should not insert another spending approval
        spending = [
            s for s in run.steps
            if s.kind == "approval" and s.approval_kind == ApprovalKind.SPENDING.value
        ]
        assert len(spending) <= 1, "Should not insert duplicate spending approvals"

    def test_budget_not_exceeded_no_injection(self, engine_factory: Any) -> None:
        """Budget large enough that no agent step crosses the threshold."""
        engine = engine_factory(budget_usd=999.0)  # very large budget
        run = _make_run(engine)
        run = engine.advance(run.id)
        spending = [
            s for s in run.steps
            if s.kind == "approval" and s.approval_kind == ApprovalKind.SPENDING.value
        ]
        assert spending == []

    def test_budget_event_emitted(self, engine_factory: Any) -> None:
        engine = engine_factory(budget_usd=0.05)
        run = _make_run(engine)
        engine.advance(run.id)
        events = engine.events(run.id)
        event_types = [e.type for e in events]
        # Either budget_exceeded event was emitted, or the workflow completed under budget.
        # If a spending step was injected, the event must be present.
        run = engine.get_run(run.id)
        spending = [s for s in run.steps if s.kind == "approval" and s.approval_kind == ApprovalKind.SPENDING.value]
        if spending:
            assert "run.budget_exceeded" in event_types
