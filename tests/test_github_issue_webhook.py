"""Tests for GitHub issues.opened -> arvino Run creation (task t_2c1a2492).

Covers:
  1. parsers.parse_git: issues event -> IssueUpserted (both github and gitea)
  2. dispatch._infer_profile: label/title-based profile selection
  3. Dispatcher.handle: end-to-end from IssueUpserted -> Run created in engine
  4. /webhooks/github HTTP endpoint: issues event delivery creates a Run
"""
from __future__ import annotations

import json

import pytest

from arvino.domain import Profile
from arvino.webhooks import parsers
from arvino.webhooks.dispatch import Dispatcher, _infer_profile
from arvino.webhooks.events import IssueUpserted
from tests import payloads as P
from tests.conftest import build_env


# ---------------------------------------------------------------------------
# parsers.parse_git — issues event
# ---------------------------------------------------------------------------


def test_parse_git_github_issue_opened() -> None:
    body = P.raw(P.github_issue(number=42, title="Add widget", labels=["bug", "p1-high"]))
    [ev] = parsers.parse_git("github", "issues", body)
    assert isinstance(ev, IssueUpserted)
    assert ev.source == "github"
    assert ev.external_id == "42"
    assert ev.repo == "Arvingrep/firebird-sea"
    assert ev.title == "Add widget"
    assert ev.labels == ["bug", "p1-high"]
    assert ev.action == "opened"
    assert ev.state_group == "started"


def test_parse_git_gitea_issue_opened() -> None:
    body = P.raw(P.github_issue(number=7, title="Infra setup", repo="acme/app"))
    [ev] = parsers.parse_git("gitea", "issues", body)
    assert isinstance(ev, IssueUpserted)
    assert ev.source == "gitea"
    assert ev.external_id == "7"
    assert ev.repo == "acme/app"


def test_parse_git_closed_issue_maps_to_completed() -> None:
    body = P.raw(P.github_issue(number=10, state="closed", action="closed"))
    [ev] = parsers.parse_git("github", "issues", body)
    assert isinstance(ev, IssueUpserted)
    assert ev.state_group == "completed"
    assert ev.action == "closed"


def test_parse_git_issues_event_missing_issue_field_raises() -> None:
    bad = {"action": "opened", "repository": {"full_name": "a/b"}}
    with pytest.raises(parsers.PayloadError):
        parsers.parse_git("github", "issues", P.raw(bad))


# ---------------------------------------------------------------------------
# _infer_profile
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "labels,title,expected",
    [
        # bmad:story -> Standard
        (["bmad:story", "epic/payment"], "Add payment flow", Profile.STANDARD),
        # architecture keyword in label -> Governed
        (["architecture", "p0-urgent"], "Some task", Profile.GOVERNED),
        # infra in title -> Governed
        ([], "Refactor infra pipeline", Profile.GOVERNED),
        # epic/infra label -> Governed
        (["epic/infra"], "Some task", Profile.GOVERNED),
        # bug label -> Fast
        (["bug", "p1-high"], "Login crash", Profile.FAST),
        # fix in title -> Fast
        ([], "fix memory leak in payment service", Profile.FAST),
        # hotfix label -> Fast
        (["hotfix"], "anything", Profile.FAST),
        # fallback -> Standard
        (["epic/payment", "p2-medium"], "Add wallet", Profile.STANDARD),
        ([], "Implement user profile page", Profile.STANDARD),
        # bmad:story wins over bug label
        (["bmad:story", "bug"], "Some story", Profile.STANDARD),
        # Governed wins over bug label
        (["architecture", "bug"], "Some task", Profile.GOVERNED),
    ],
)
def test_infer_profile(labels: list[str], title: str, expected: Profile) -> None:
    assert _infer_profile(labels, title) == expected


# ---------------------------------------------------------------------------
# Dispatcher.handle — integration (in-memory engine)
# ---------------------------------------------------------------------------


def _make_env_with_github_project():
    """Build a test env whose project has repo_full_name=Arvingrep/firebird-sea."""
    env = build_env()
    # Create a second project mapped to the GitHub repo
    project = env.engine.create_project(
        "firebird",
        "Firebird Sea",
        repo_full_name="Arvingrep/firebird-sea",
    )
    return env, project


def test_dispatch_github_issue_creates_fast_run() -> None:
    env, project = _make_env_with_github_project()
    dispatcher = Dispatcher(env.engine)

    ev = IssueUpserted(
        source="github",
        action="opened",
        external_id="99",
        repo="Arvingrep/firebird-sea",
        title="fix login crash",
        labels=["bug"],
        state_group="started",
    )
    result = dispatcher.handle(ev)
    assert result.status == "handled"
    assert result.run_ids
    run = env.engine.get_run(result.run_ids[0])
    assert run.profile == Profile.FAST


def test_dispatch_github_issue_creates_standard_run_for_story() -> None:
    env, project = _make_env_with_github_project()
    dispatcher = Dispatcher(env.engine)

    ev = IssueUpserted(
        source="github",
        action="opened",
        external_id="100",
        repo="Arvingrep/firebird-sea",
        title="Add user profile page",
        labels=["bmad:story", "epic/payment"],
        state_group="started",
    )
    result = dispatcher.handle(ev)
    assert result.status == "handled"
    run = env.engine.get_run(result.run_ids[0])
    assert run.profile == Profile.STANDARD


def test_dispatch_github_issue_creates_governed_run_for_arch() -> None:
    env, project = _make_env_with_github_project()
    dispatcher = Dispatcher(env.engine)

    ev = IssueUpserted(
        source="github",
        action="opened",
        external_id="101",
        repo="Arvingrep/firebird-sea",
        title="Architecture review for new service mesh",
        labels=["architecture"],
        state_group="started",
    )
    result = dispatcher.handle(ev)
    assert result.status == "handled"
    run = env.engine.get_run(result.run_ids[0])
    assert run.profile == Profile.GOVERNED


def test_dispatch_github_closed_issue_does_not_create_run() -> None:
    env, project = _make_env_with_github_project()
    dispatcher = Dispatcher(env.engine)

    ev = IssueUpserted(
        source="github",
        action="closed",
        external_id="200",
        repo="Arvingrep/firebird-sea",
        title="Old fix",
        labels=["bug"],
        state_group="completed",  # closed -> completed -> no run
    )
    result = dispatcher.handle(ev)
    assert result.status == "handled"
    assert result.run_ids == []


def test_dispatch_github_issue_unknown_repo_ignored() -> None:
    env = build_env()
    dispatcher = Dispatcher(env.engine)

    ev = IssueUpserted(
        source="github",
        action="opened",
        external_id="42",
        repo="unknown/norepo",
        title="Does not matter",
        state_group="started",
    )
    result = dispatcher.handle(ev)
    assert result.status == "ignored"


def test_dispatch_idempotent_second_open_returns_existing_run() -> None:
    env, project = _make_env_with_github_project()
    dispatcher = Dispatcher(env.engine)

    ev = IssueUpserted(
        source="github",
        action="opened",
        external_id="300",
        repo="Arvingrep/firebird-sea",
        title="fix widget crash",
        labels=["bug"],
        state_group="started",
    )
    r1 = dispatcher.handle(ev)
    r2 = dispatcher.handle(ev)
    assert r1.run_ids == r2.run_ids
    assert r2.detail == "run already exists"


# ---------------------------------------------------------------------------
# /webhooks/github HTTP endpoint — issues event
# ---------------------------------------------------------------------------


def test_http_github_issues_webhook_creates_run() -> None:
    from fastapi.testclient import TestClient

    from arvino.api import create_app
    from arvino.settings import Settings

    env = build_env()
    # Add GitHub-mapped project
    env.engine.create_project("firebird", "Firebird Sea", repo_full_name="Arvingrep/firebird-sea")

    settings = Settings(
        database_url="sqlite://",
        github_webhook_secret="gh-test-secret",  # type: ignore[arg-type]
    )
    app = create_app(env.engine, settings)
    client = TestClient(app, raise_server_exceptions=True)

    payload = P.github_issue(
        number=55,
        title="fix checkout crash",
        labels=["bug"],
        repo="Arvingrep/firebird-sea",
    )
    body = P.raw(payload)
    headers = P.hmac_headers("gh-test-secret", body, "x-hub-signature-256", prefix="sha256=")
    headers["x-github-event"] = "issues"
    headers["x-github-delivery"] = "test-delivery-gh-issues-001"

    resp = client.post("/webhooks/github", content=body, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "processed"
    results = data["results"]
    assert len(results) == 1
    assert results[0]["status"] == "handled"
    assert results[0]["run_ids"]  # a run was created


def test_http_github_issues_closed_no_run_created() -> None:
    from fastapi.testclient import TestClient

    from arvino.api import create_app
    from arvino.settings import Settings

    env = build_env()
    env.engine.create_project("firebird", "Firebird Sea", repo_full_name="Arvingrep/firebird-sea")

    settings = Settings(
        database_url="sqlite://",
        github_webhook_secret="gh-test-secret",  # type: ignore[arg-type]
    )
    app = create_app(env.engine, settings)
    client = TestClient(app, raise_server_exceptions=True)

    payload = P.github_issue(
        number=56,
        title="closed issue",
        action="closed",
        state="closed",
        repo="Arvingrep/firebird-sea",
    )
    body = P.raw(payload)
    headers = P.hmac_headers("gh-test-secret", body, "x-hub-signature-256", prefix="sha256=")
    headers["x-github-event"] = "issues"
    headers["x-github-delivery"] = "test-delivery-gh-issues-002"

    resp = client.post("/webhooks/github", content=body, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    results = data["results"]
    assert results[0]["run_ids"] == []


def test_http_github_other_events_still_ignored_after_change() -> None:
    """push, issue_comment etc. still return ignored/empty as before."""
    from fastapi.testclient import TestClient

    from arvino.api import create_app
    from arvino.settings import Settings

    env = build_env()
    settings = Settings(
        database_url="sqlite://",
        github_webhook_secret="gh-test-secret",  # type: ignore[arg-type]
    )
    app = create_app(env.engine, settings)
    client = TestClient(app, raise_server_exceptions=True)

    # push event: repository field present but no issue field → returns [] from parser
    push_payload = {"repository": {"full_name": "acme/app"}, "ref": "refs/heads/main"}
    body = P.raw(push_payload)
    headers = P.hmac_headers("gh-test-secret", body, "x-hub-signature-256", prefix="sha256=")
    headers["x-github-event"] = "push"
    headers["x-github-delivery"] = "push-001"

    resp = client.post("/webhooks/github", content=body, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
