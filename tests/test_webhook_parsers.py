"""Contract tests for the pure parsers and auth helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arvino.webhooks import auth, parsers
from arvino.webhooks.events import (
    AlertReceived,
    CheckCompleted,
    DeploymentStatus,
    PullRequestUpdated,
)
from tests import payloads as P

FIXTURES = Path(__file__).parent / "fixtures" / "webhooks"


def _load(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _raw(d: dict) -> bytes:
    return json.dumps(d, separators=(",", ":")).encode()


def test_hmac_accepts_valid_and_rejects_everything_else() -> None:
    body = b'{"a":1}'
    good = auth.sign("s3cret", body)
    auth.verify_hmac_sha256("s3cret", body, good)
    auth.verify_hmac_sha256("s3cret", body, "sha256=" + good, prefix="sha256=")
    for secret, hdr in [("wrong", good), ("s3cret", "0" * 64), ("s3cret", None), ("s3cret", "")]:
        with pytest.raises(auth.WebhookAuthError):
            auth.verify_hmac_sha256(secret, body, hdr)
    with pytest.raises(auth.WebhookAuthError):
        auth.verify_hmac_sha256("s3cret", b'{"a":2}', good)  # body tampered
    with pytest.raises(auth.WebhookAuthError):
        auth.verify_hmac_sha256("s3cret", body, good, prefix="sha256=")  # prefix required


@pytest.mark.parametrize("secret", [None, ""])
def test_unconfigured_secret_rejects_even_a_correct_looking_signature(secret: str | None) -> None:
    body = b"{}"
    with pytest.raises(auth.WebhookAuthError):
        auth.verify_hmac_sha256(secret, body, auth.sign("", body))
    with pytest.raises(auth.WebhookAuthError):
        auth.verify_shared_token(secret, "")


def test_shared_token() -> None:
    auth.verify_shared_token("tok", "tok")
    for bad in ("tok2", "", None):
        with pytest.raises(auth.WebhookAuthError):
            auth.verify_shared_token("tok", bad)


def test_git_pull_request_gitea_and_github_shapes() -> None:
    body = P.raw(
        P.git_pr(
            "arvino/run-aaaaaaaaaaaa/pull_request",
            "c" * 40,
            merged=True,
            merged_by="owner",
            merge_sha="d" * 40,
        )
    )
    for provider in ("gitea", "github"):
        [ev] = parsers.parse_git(provider, "pull_request", body)
        assert isinstance(ev, PullRequestUpdated)
        assert (ev.source, ev.repo, ev.number, ev.merged, ev.merged_by, ev.merge_sha) == (
            provider,
            "acme/app",
            1,
            True,
            "owner",
            "d" * 40,
        )


def test_git_status_and_check_run_normalise_conclusions() -> None:
    cases = {
        "success": "success",
        "failure": "failure",
        "error": "failure",
        "pending": "pending",
        "skipped": "failure",
    }
    for state, want in cases.items():
        [ev] = parsers.parse_git(
            "gitea", "status", P.raw(P.git_status("a" * 40, "unit-tests", state))
        )
        assert isinstance(ev, CheckCompleted) and ev.conclusion == want
    [ev] = parsers.parse_git(
        "github", "check_run", P.raw(P.github_check_run("a" * 40, "lint", "neutral"))
    )
    assert (
        isinstance(ev, CheckCompleted) and ev.conclusion == "failure"
    )  # not clearly green => not success
    pending = P.github_check_run("a" * 40, "lint")
    pending["action"] = "created"
    [ev] = parsers.parse_git("github", "check_run", P.raw(pending))
    assert isinstance(ev, CheckCompleted) and ev.conclusion == "pending"


def test_git_unconsumed_events_are_ignored() -> None:
    assert parsers.parse_git("gitea", "push", P.raw({"repository": {"full_name": "a/b"}})) == []


def test_ci_completion_contract() -> None:
    evs = parsers.parse_ci(
        P.raw(
            P.ci_completion(
                "a" * 40, {"unit-tests": "success", "lint": "failure"}, branch="arvino/x/y"
            )
        )
    )
    assert [(e.name, e.conclusion) for e in evs if isinstance(e, CheckCompleted)] == [
        ("unit-tests", "success"),
        ("lint", "failure"),
    ]
    for bad in ({"repo": "a/b", "sha": "abc"}, {"repo": "a/b", "sha": "a" * 40, "checks": []}):
        with pytest.raises(parsers.PayloadError):
            parsers.parse_ci(P.raw(bad))


def test_argocd_contract() -> None:
    [ev] = parsers.parse_argocd(P.raw(P.argocd_status("acme-preview", "a" * 40)))
    assert isinstance(ev, DeploymentStatus) and ev.health == "Healthy"
    with pytest.raises(parsers.PayloadError):
        parsers.parse_argocd(P.raw({"app": "x"}))


def test_nightingale_contract() -> None:
    [ev] = parsers.parse_nightingale(P.raw(P.nightingale_alert()))
    assert isinstance(ev, AlertReceived)
    assert (ev.alert_id, ev.project_key, ev.severity, ev.recovered) == ("4242", "acme", 1, False)
    bad = P.nightingale_alert()
    bad["severity"] = 9
    with pytest.raises(parsers.PayloadError):
        parsers.parse_nightingale(json.dumps(bad).encode())


# ---------------------------------------------------------------------------
# Fixture-based tests: each real fixture is parsed and verified.
# ---------------------------------------------------------------------------


def test_fixture_gitea_pr_opened() -> None:
    [ev] = parsers.parse_git("gitea", "pull_request", _load("gitea_pr_opened.json"))
    assert isinstance(ev, PullRequestUpdated)
    assert ev.source == "gitea"
    assert ev.repo == "acme/app"
    assert ev.number == 42
    assert ev.action == "opened"
    assert ev.merged is False
    assert ev.head_ref == "arvino/run-aaaaaaaaaaaa/pull_request"
    assert ev.head_sha == "cccccccccccccccccccccccccccccccccccccccc"


def test_fixture_gitea_pr_merged() -> None:
    [ev] = parsers.parse_git("gitea", "pull_request", _load("gitea_pr_merged.json"))
    assert isinstance(ev, PullRequestUpdated)
    assert ev.merged is True
    assert ev.merged_by == "owner"
    assert ev.merge_sha == "dddddddddddddddddddddddddddddddddddddddd"
    assert ev.action == "closed"


def test_fixture_gitea_status_success() -> None:
    [ev] = parsers.parse_git("gitea", "status", _load("gitea_status_success.json"))
    assert isinstance(ev, CheckCompleted)
    assert ev.conclusion == "success"
    assert ev.name == "unit-tests"
    assert ev.sha == "cccccccccccccccccccccccccccccccccccccccc"


def test_fixture_gitea_status_failure() -> None:
    [ev] = parsers.parse_git("gitea", "status", _load("gitea_status_failure.json"))
    assert isinstance(ev, CheckCompleted)
    assert ev.conclusion == "failure"
    assert ev.name == "lint"


def test_fixture_github_check_run_success() -> None:
    [ev] = parsers.parse_git("github", "check_run", _load("github_check_run_success.json"))
    assert isinstance(ev, CheckCompleted)
    assert ev.conclusion == "success"
    assert ev.name == "unit-tests"


def test_fixture_github_check_run_pending() -> None:
    [ev] = parsers.parse_git("github", "check_run", _load("github_check_run_pending.json"))
    assert isinstance(ev, CheckCompleted)
    assert ev.conclusion == "pending"
    assert ev.name == "lint"


def test_fixture_ci_completion_mixed() -> None:
    evs = parsers.parse_ci(_load("ci_completion_mixed.json"))
    checks = {e.name: e.conclusion for e in evs if isinstance(e, CheckCompleted)}
    assert checks == {"unit-tests": "success", "lint": "failure", "secret-scan": "success"}
    assert len(evs) == 3


def test_fixture_ci_completion_all_success() -> None:
    evs = parsers.parse_ci(_load("ci_completion_all_success.json"))
    for e in evs:
        assert isinstance(e, CheckCompleted) and e.conclusion == "success"
    assert len(evs) == 5


def test_fixture_argocd_status_healthy() -> None:
    [ev] = parsers.parse_argocd(_load("argocd_status_healthy.json"))
    assert isinstance(ev, DeploymentStatus)
    assert ev.app == "acme-preview"
    assert ev.health == "Healthy"
    assert ev.sync_status == "Synced"


def test_fixture_argocd_status_production() -> None:
    [ev] = parsers.parse_argocd(_load("argocd_status_production.json"))
    assert isinstance(ev, DeploymentStatus)
    assert ev.app == "acme-production"
    assert ev.health == "Healthy"


def test_fixture_argocd_status_degraded() -> None:
    [ev] = parsers.parse_argocd(_load("argocd_status_degraded.json"))
    assert isinstance(ev, DeploymentStatus)
    assert ev.health == "Degraded"
    assert ev.sync_status == "OutOfSync"


def test_fixture_nightingale_alert_critical() -> None:
    [ev] = parsers.parse_nightingale(_load("nightingale_alert_critical.json"))
    assert isinstance(ev, AlertReceived)
    assert ev.alert_id == "4242"
    assert ev.project_key == "acme"
    assert ev.severity == 1
    assert ev.recovered is False
    assert ev.rule_name == "HighErrorRate"


def test_fixture_nightingale_alert_recovered() -> None:
    [ev] = parsers.parse_nightingale(_load("nightingale_alert_recovered.json"))
    assert isinstance(ev, AlertReceived)
    assert ev.alert_id == "5001"
    assert ev.severity == 2
    assert ev.recovered is True
    assert ev.rule_name == "HighMemoryUsage"
