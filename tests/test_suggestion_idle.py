"""Tests for suggestion intake API and idle work-filling scheduler."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from arvino.api import create_app
from arvino.domain import Profile
from arvino.idle_scheduler import IdleScheduler
from arvino.ports import StepOutcome
from arvino.settings import Settings
from tests.conftest import Env
from tests.payloads import OWNER_TOKEN

OWNER_H = {"Authorization": f"Bearer {OWNER_TOKEN}"}


def make_settings(**over: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "sqlite://",
        "owner_token": SecretStr(OWNER_TOKEN),
        "agent_token": SecretStr("agent-test-token"),
        "reconcile_interval_seconds": 0,
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture
def api(env: Env) -> tuple[TestClient, Env]:
    return TestClient(create_app(env.engine, make_settings())), env


# ---------------------------------------------------------------------------
# B3.1: record_suggestion creates an issue with label 'backlog'
# ---------------------------------------------------------------------------


def test_record_suggestion_creates_backlog_issue(env: Env) -> None:
    suggestion = env.engine.record_suggestion(env.project_id, "Add dark mode")
    assert "backlog" in suggestion.labels
    assert suggestion.state == "backlog"
    assert suggestion.kind == "suggestion"
    assert suggestion.title == "Add dark mode"


# ---------------------------------------------------------------------------
# B3.2: record_suggestion with phase=6 adds label 'backlog:phase-6'
# ---------------------------------------------------------------------------


def test_record_suggestion_with_phase_adds_phase_label(env: Env) -> None:
    suggestion = env.engine.record_suggestion(env.project_id, "Refactor auth", phase=6)
    assert "backlog" in suggestion.labels
    assert "backlog:phase-6" in suggestion.labels


# ---------------------------------------------------------------------------
# B3.3: record_suggestion does NOT start a run
# ---------------------------------------------------------------------------


def test_record_suggestion_does_not_start_run(env: Env) -> None:
    project_runs_before = env.engine.list_runs(project_id=env.project_id)
    env.engine.record_suggestion(env.project_id, "Improve logging")
    project_runs_after = env.engine.list_runs(project_id=env.project_id)
    assert len(project_runs_after) == len(project_runs_before)


# ---------------------------------------------------------------------------
# B3.4: POST /api/v1/projects/{id}/suggestions returns 201 with IssueView
# ---------------------------------------------------------------------------


def test_api_post_suggestion_returns_201(api: tuple[TestClient, Env]) -> None:
    c, env = api
    resp = c.post(
        f"/projects/{env.project_id}/suggestions",
        json={"title": "Add search", "body": "Global search feature", "phase": 2},
        headers=OWNER_H,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Add search"
    assert body["state"] == "backlog"
    assert "backlog" in body["labels"]
    assert "backlog:phase-2" in body["labels"]


# ---------------------------------------------------------------------------
# B3.5: POST /suggestions with unknown project_id returns 404
# ---------------------------------------------------------------------------


def test_api_post_suggestion_unknown_project_404(api: tuple[TestClient, Env]) -> None:
    c, _ = api
    resp = c.post(
        "/projects/nonexistent-prj/suggestions",
        json={"title": "Ghost suggestion"},
        headers=OWNER_H,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# B3.6: list_issues returns all issues for a project
# ---------------------------------------------------------------------------


def test_list_issues_returns_all_project_issues(env: Env) -> None:
    env.engine.record_suggestion(env.project_id, "A")
    env.engine.record_suggestion(env.project_id, "B")
    issues = env.engine.list_issues(env.project_id)
    # The env fixture creates one issue ("Add widget"), plus the two above
    assert len(issues) >= 3
    titles = {i.title for i in issues}
    assert "A" in titles
    assert "B" in titles
    assert "Add widget" in titles


# ---------------------------------------------------------------------------
# B3.7: IdleScheduler.fill_idle starts a run for a backlog issue
# ---------------------------------------------------------------------------


def test_fill_idle_starts_run_for_backlog_issue(env: Env) -> None:
    scheduler = IdleScheduler(env.engine)
    env.engine.record_suggestion(env.project_id, "Fix memory leak")
    started = scheduler.fill_idle()
    assert len(started) == 1
    run = env.engine.get_run(started[0])
    assert run.requested_by == "system:idle_scheduler"
    # Idempotency: calling again returns empty.
    assert scheduler.fill_idle() == []


# ---------------------------------------------------------------------------
# B3.8: IdleScheduler.fill_idle does not exceed MAX_BACKLOG_RUNS_PER_PROJECT
# ---------------------------------------------------------------------------


def test_fill_idle_does_not_exceed_max(env: Env) -> None:
    scheduler = IdleScheduler(env.engine)
    for i in range(5):
        env.engine.record_suggestion(env.project_id, f"Backlog item {i}")

    started = scheduler.fill_idle()
    assert len(started) <= 2
    for rid in started:
        run = env.engine.get_run(rid)
        assert run.requested_by == "system:idle_scheduler"


# ---------------------------------------------------------------------------
# B3.9: IdleScheduler.fill_idle skips issues that already have an active run
# ---------------------------------------------------------------------------


def test_fill_idle_skips_issues_with_active_run(env: Env) -> None:
    scheduler = IdleScheduler(env.engine)
    suggestion = env.engine.record_suggestion(env.project_id, "Skip me")

    # Script triage to stay running so the run remains active.
    env.executor.script("triage", StepOutcome(status="running"))
    run, created = env.engine.create_run(
        env.project_id,
        suggestion.id,
        Profile.FAST,
        f"manual:{suggestion.id}",
        "test",
    )
    assert created
    env.engine.advance(run.id)  # dispatches triage, stays running

    # fill_idle should skip this issue because it has an active run.
    started = scheduler.fill_idle()
    assert started == []


# ---------------------------------------------------------------------------
# B3.10: IdleScheduler.fill_idle returns empty list when no backlog issues
# ---------------------------------------------------------------------------


def test_fill_idle_empty_when_no_backlog_issues(env: Env) -> None:
    scheduler = IdleScheduler(env.engine)
    assert scheduler.fill_idle() == []