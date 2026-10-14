"""Contract tests for the pure parsers and auth helpers."""

from __future__ import annotations

import json

import pytest

from arvino.webhooks import auth, parsers
from arvino.webhooks.events import (
    AlertReceived,
    CheckCompleted,
    DeploymentStatus,
    IssueUpserted,
    PullRequestUpdated,
)
from tests import payloads as P


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


def test_plane_issue_parsed() -> None:
    [ev] = parsers.parse_plane(P.raw(P.plane_issue(labels=["arvino:fast", "bug"])))
    assert isinstance(ev, IssueUpserted)
    assert (ev.external_id, ev.plane_project_id, ev.labels, ev.state_group) == (
        "11111111-aaaa",
        "plane-proj-1",
        ["arvino:fast", "bug"],
        "unstarted",
    )


def test_plane_other_events_are_ignored_and_garbage_rejected() -> None:
    assert parsers.parse_plane(P.raw({"event": "cycle", "action": "created", "data": {}})) == []
    with pytest.raises(parsers.PayloadError):
        parsers.parse_plane(b"not json")
    with pytest.raises(parsers.PayloadError):
        parsers.parse_plane(P.raw({"event": "issue", "action": "created", "data": {"id": "x"}}))


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
