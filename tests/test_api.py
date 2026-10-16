"""HTTP API: authn/authz, run lifecycle, approvals, webhooks end to end (engine behind FastAPI)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from arvino.api import create_app
from arvino.settings import Settings
from arvino.workflows.model import load_registry  # noqa: F401
from tests import payloads as P
from tests.conftest import ALL_CHECKS, BASE_CHECKS, REPO, SHA, Env, build_env

OWNER_H = {"Authorization": f"Bearer {P.OWNER_TOKEN}"}
AGENT_H = {"Authorization": f"Bearer {P.AGENT_TOKEN}"}


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "sqlite://",
        "owner_token": SecretStr(P.OWNER_TOKEN),
        "agent_token": SecretStr(P.AGENT_TOKEN),
        "gitea_webhook_secret": SecretStr(P.GITEA_SECRET),
        "github_webhook_secret": SecretStr(P.GITHUB_SECRET),
        "ci_webhook_token": SecretStr(P.CI_TOKEN),
        "argocd_webhook_token": SecretStr(P.ARGO_TOKEN),
        "nightingale_webhook_token": SecretStr(P.N9E_TOKEN),
        "reconcile_interval_seconds": 0,
    }
    base.update(over)
    return Settings(**base)


@pytest.fixture
def api(env: Env) -> tuple[TestClient, Env]:
    return TestClient(create_app(env.engine, make_settings())), env


def post_signed(
    c: TestClient,
    path: str,
    payload: dict[str, Any],
    secret: str,
    header: str,
    *,
    prefix: str = "",
    extra: dict[str, str] | None = None,
) -> Any:
    body = P.raw(payload)
    headers = {
        header: prefix + P.sign(secret, body),
        "content-type": "application/json",
        **(extra or {}),
    }
    return c.post(path, content=body, headers=headers)


def post_token(
    c: TestClient,
    path: str,
    payload: dict[str, Any],
    token: str,
    extra: dict[str, str] | None = None,
) -> Any:
    return c.post(
        path,
        content=P.raw(payload),
        headers={"X-Arvino-Token": token, "content-type": "application/json", **(extra or {})},
    )


P.sign = __import__("arvino.webhooks.auth", fromlist=["sign"]).sign  # type: ignore[attr-defined]


# ----------------------------------------------------------------------------- authn / authz


def test_health_is_open_everything_else_needs_a_token(api: tuple[TestClient, Env]) -> None:
    c, _ = api
    assert c.get("/healthz").status_code == 200
    assert c.get("/readyz").json()["status"] == "ready"
    for path in ("/projects", "/runs", "/approvals", "/workflows", "/work"):
        assert c.get(path).status_code == 401
        assert c.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_unconfigured_tokens_cannot_authenticate(env: Env) -> None:
    c = TestClient(create_app(env.engine, make_settings(owner_token=None, agent_token=None)))
    assert c.get("/projects", headers={"Authorization": "Bearer "}).status_code == 401
    assert c.get("/projects", headers=OWNER_H).status_code == 401


def test_agent_token_cannot_create_projects_or_runs(api: tuple[TestClient, Env]) -> None:
    c, env = api
    body = {"key": "new", "name": "New"}
    assert c.post("/projects", json=body, headers=AGENT_H).status_code == 403
    assert c.post("/projects", json=body, headers=OWNER_H).status_code == 201
    run = {"project_id": env.project_id, "issue_id": env.issue_id, "profile": "fast"}
    assert c.post("/runs", json=run, headers=AGENT_H).status_code == 403


def test_duplicate_project_is_409(api: tuple[TestClient, Env]) -> None:
    c, _ = api
    assert c.post("/projects", json={"key": "dup", "name": "D"}, headers=OWNER_H).status_code == 201
    assert c.post("/projects", json={"key": "dup", "name": "D"}, headers=OWNER_H).status_code == 409


def test_unknown_resources_are_404(api: tuple[TestClient, Env]) -> None:
    c, _ = api
    assert c.get("/runs/run-doesnotexist", headers=OWNER_H).status_code == 404
    assert c.post("/runs/run-nope/advance", headers=OWNER_H).status_code == 404


# ----------------------------------------------------------------------------- run lifecycle


def test_run_lifecycle_and_idempotent_creation(api: tuple[TestClient, Env]) -> None:
    c, env = api
    body = {
        "project_id": env.project_id,
        "issue_id": env.issue_id,
        "profile": "fast",
        "idempotency_key": "x",
    }
    r1 = c.post("/runs", json=body, headers=OWNER_H)
    r2 = c.post("/runs", json=body, headers=OWNER_H)
    assert r1.status_code == r2.status_code == 201 and r1.json()["id"] == r2.json()["id"]
    run_id = r1.json()["id"]
    got = c.get(f"/runs/{run_id}", headers=AGENT_H).json()
    assert got["profile"] == "fast" and {s["key"] for s in got["steps"]} >= {
        "triage",
        "ci",
        "preview",
    }
    assert c.get(f"/runs/{run_id}/events", headers=OWNER_H).json()[0]["type"] == "run.created"
    assert [r["id"] for r in c.get("/runs?active=true", headers=OWNER_H).json()] == [run_id]
    assert c.post(f"/runs/{run_id}/cancel", headers=AGENT_H).status_code == 403
    cancelled = c.post(f"/runs/{run_id}/cancel", headers=OWNER_H).json()
    assert cancelled["status"] == "cancelled"
    assert c.get("/runs?active=true", headers=OWNER_H).json() == []


def test_agent_harness_pull_model_work_and_completion(env: Env) -> None:
    from arvino.fakes import QueueExecutor

    env.engine.ports.executor = QueueExecutor()
    c = TestClient(create_app(env.engine, make_settings()))
    run = c.post(
        "/runs",
        json={"project_id": env.project_id, "issue_id": env.issue_id, "profile": "fast"},
        headers=OWNER_H,
    ).json()
    [item] = c.get("/work?role=triage", headers=AGENT_H).json()
    assert (item["run_id"], item["step_key"], item["actor"]) == (
        run["id"],
        "triage",
        "agent:triage",
    )
    done = c.post(
        f"/runs/{run['id']}/steps/triage/complete",
        json={"attempt": item["attempt"], "status": "succeeded"},
        headers=AGENT_H,
    )
    assert done.json() == {"result": "applied"}
    again = c.post(
        f"/runs/{run['id']}/steps/triage/complete",
        json={"attempt": item["attempt"], "status": "succeeded"},
        headers=AGENT_H,
    )
    assert again.json() == {"result": "duplicate"}
    assert [w["step_key"] for w in c.get("/work", headers=AGENT_H).json()] == ["develop"]
    stale = c.post(
        f"/runs/{run['id']}/steps/develop/complete",
        json={"attempt": 99, "status": "succeeded"},
        headers=AGENT_H,
    )
    assert stale.json() == {"result": "stale"}


def test_approval_endpoint_forbids_agents_and_allows_owner(env: Env) -> None:
    env.executor.script("pull_request", env.pr_script())
    c = TestClient(create_app(env.engine, make_settings()))
    run = env.start(__import__("arvino.domain", fromlist=["Profile"]).Profile.STANDARD)
    env.webhook_pr(run.id)
    env.checks(run.id, ALL_CHECKS)
    env.converge("acme-preview")
    [appr] = c.get("/approvals?status=pending", headers=OWNER_H).json()
    denied = c.post(
        f"/approvals/{appr['id']}/decision", json={"decision": "approved"}, headers=AGENT_H
    )
    assert denied.status_code == 403 and any("human" in r for r in denied.json()["reasons"])
    assert c.get(f"/runs/{run.id}", headers=OWNER_H).json()["status"] == "waiting_approval"
    ok = c.post(
        f"/approvals/{appr['id']}/decision",
        json={"decision": "approved", "reason": "ship"},
        headers=OWNER_H,
    )
    assert ok.status_code == 200 and ok.json()["decided_by"]["id"] == "owner"
    again = c.post(
        f"/approvals/{appr['id']}/decision", json={"decision": "rejected"}, headers=OWNER_H
    )
    assert again.status_code == 409


def test_workflows_listing_exposes_all_profiles(api: tuple[TestClient, Env]) -> None:
    c, _ = api
    assert {w["profile"] for w in c.get("/workflows", headers=OWNER_H).json()} == {
        "fast",
        "standard",
        "governed",
    }


# ----------------------------------------------------------------------------- webhook security


def test_webhooks_reject_bad_or_missing_credentials(api: tuple[TestClient, Env]) -> None:
    c, _ = api
    body = P.raw(P.github_issue(labels=["arvino:fast"]))
    assert c.post("/webhooks/github", content=body).status_code == 401
    assert (
        c.post(
            "/webhooks/github", content=body, headers={"X-Hub-Signature-256": "sha256=" + "0" * 64}
        ).status_code
        == 401
    )
    assert (
        c.post(
            "/webhooks/github", content=b"{}", headers={"X-Hub-Signature-256": "sha256=" + "0" * 64}
        ).status_code
        == 401
    )
    assert (
        c.post(
            "/webhooks/gitea", content=b"{}", headers={"X-Gitea-Signature": "0" * 64}
        ).status_code
        == 401
    )
    for path in ("ci", "argocd", "nightingale"):
        assert c.post(f"/webhooks/{path}", content=b"{}").status_code == 401
        assert (
            c.post(
                f"/webhooks/{path}", content=b"{}", headers={"X-Arvino-Token": "wrong"}
            ).status_code
            == 401
        )


def test_webhook_source_without_configured_secret_is_rejected(env: Env) -> None:
    c = TestClient(
        create_app(env.engine, make_settings(github_webhook_secret=None, ci_webhook_token=None))
    )
    r = post_signed(
        c, "/webhooks/github", P.github_issue(), "", "X-Hub-Signature-256", prefix="sha256="
    )
    assert r.status_code == 401
    assert (
        post_token(c, "/webhooks/ci", P.ci_completion(SHA, {"lint": "success"}), "").status_code
        == 401
    )


def test_oversized_payload_rejected(api: tuple[TestClient, Env]) -> None:
    c, _ = api
    big = b"{" + b'"a":"' + b"x" * 1_100_000 + b'"}'
    r = c.post("/webhooks/ci", content=big, headers={"X-Arvino-Token": P.CI_TOKEN})
    assert r.status_code == 413


def test_invalid_payload_is_422_and_recorded_as_rejected(api: tuple[TestClient, Env]) -> None:
    c, _ = api
    r = post_token(
        c, "/webhooks/ci", {"repo": "acme/app"}, P.CI_TOKEN, {"X-Arvino-Delivery": "bad-1"}
    )
    assert r.status_code == 422
    from sqlalchemy import select

    from arvino.db import WebhookDeliveryRow, transaction

    with transaction(api[1].engine._sf) as s:
        row = s.scalar(select(WebhookDeliveryRow).where(WebhookDeliveryRow.delivery_id == "bad-1"))
        assert row is not None and row.status == "rejected"


# ----------------------------------------------------------------------------- GitHub issue webhooks


def test_github_issue_label_starts_run_and_replays_are_idempotent(
    api: tuple[TestClient, Env],
) -> None:
    c, env = api
    payload = P.github_issue(labels=["arvino:fast"], repo="acme/app")
    h = {"X-GitHub-Delivery": "d-1", "X-GitHub-Event": "issues"}
    r1 = post_signed(
        c,
        "/webhooks/github",
        payload,
        P.GITHUB_SECRET,
        "X-Hub-Signature-256",
        prefix="sha256=",
        extra=h,
    )
    assert r1.status_code == 200 and r1.json()["results"][0]["detail"] == "run created"
    run_id = r1.json()["results"][0]["run_ids"][0]
    # Same delivery replayed: deduplicated at the delivery layer.
    r2 = post_signed(
        c,
        "/webhooks/github",
        payload,
        P.GITHUB_SECRET,
        "X-Hub-Signature-256",
        prefix="sha256=",
        extra=h,
    )
    assert r2.json()["status"] == "duplicate"
    # New delivery id, same business event: deduplicated by the run idempotency key.
    r3 = post_signed(
        c,
        "/webhooks/github",
        payload,
        P.GITHUB_SECRET,
        "X-Hub-Signature-256",
        prefix="sha256=",
        extra={"X-GitHub-Delivery": "d-2", "X-GitHub-Event": "issues"},
    )
    assert r3.json()["results"][0]["detail"] == "run already exists"
    assert r3.json()["results"][0]["run_ids"] == [run_id]
    assert len([r for r in env.engine.list_runs() if r.id == run_id]) == 1
    assert len(env.engine.list_runs()) == 1


@pytest.mark.parametrize(
    ("labels", "detail", "expect_runs"),
    [
        ([], "run created", True),  # auto-inferred as Standard
        (["bug"], "run created", True),  # auto-inferred as Fast
        (["arvino:fast", "arvino:governed"], "multiple", False),
        (["arvino:yolo"], "unknown profile", False),
    ],
)
def test_github_issue_without_exactly_one_valid_profile_label_starts_nothing(
    api: tuple[TestClient, Env], labels: list[str], detail: str, expect_runs: bool
) -> None:
    c, env = api
    r = post_signed(
        c,
        "/webhooks/github",
        P.github_issue(labels=labels, repo="acme/app"),
        P.GITHUB_SECRET,
        "X-Hub-Signature-256",
        prefix="sha256=",
        extra={"X-GitHub-Event": "issues"},
    )
    assert r.status_code == 200 and detail in r.json()["results"][0]["detail"]
    if expect_runs:
        assert len(env.engine.list_runs()) == 1
    else:
        assert env.engine.list_runs() == []


def test_github_issue_closed_and_unknown_project_start_nothing(
    api: tuple[TestClient, Env],
) -> None:
    c, env = api
    done = P.github_issue(labels=["arvino:fast"], state="closed", repo="acme/app")
    assert (
        post_signed(
            c,
            "/webhooks/github",
            done,
            P.GITHUB_SECRET,
            "X-Hub-Signature-256",
            prefix="sha256=",
            extra={"X-GitHub-Event": "issues"},
        ).status_code
        == 200
    )
    other = P.github_issue(repo="other/repo", labels=["arvino:fast"])
    r = post_signed(
        c,
        "/webhooks/github",
        other,
        P.GITHUB_SECRET,
        "X-Hub-Signature-256",
        prefix="sha256=",
        extra={"X-GitHub-Event": "issues"},
    )
    assert r.json()["results"][0]["status"] == "ignored"
    assert env.engine.list_runs() == []


# ----------------------------------------------------------------------------- full pipeline over HTTP


def test_fast_pipeline_end_to_end_over_webhooks(api: tuple[TestClient, Env]) -> None:
    c, env = api
    env.executor.script("pull_request", env.pr_script())
    r = post_signed(
        c,
        "/webhooks/github",
        P.github_issue(labels=["arvino:fast"], repo="acme/app"),
        P.GITHUB_SECRET,
        "X-Hub-Signature-256",
        prefix="sha256=",
        extra={"X-GitHub-Event": "issues"},
    )
    run_id = r.json()["results"][0]["run_ids"][0]
    branch = f"arvino/{run_id}/pull_request"

    # Gitea PR webhook (HMAC)
    pr = post_signed(
        c,
        "/webhooks/gitea",
        P.git_pr(branch, SHA),
        P.GITEA_SECRET,
        "X-Gitea-Signature",
        extra={"X-Gitea-Event": "pull_request", "X-Gitea-Delivery": "g-1"},
    )
    assert pr.status_code == 200 and pr.json()["results"][0]["run_ids"] == [run_id]

    # Required checks: two via Gitea commit statuses, one via CI completion webhook.
    for ctx in ("unit-tests", "lint"):
        st = post_signed(
            c,
            "/webhooks/gitea",
            P.git_status(SHA, ctx),
            P.GITEA_SECRET,
            "X-Gitea-Signature",
            extra={"X-Gitea-Event": "status", "X-Gitea-Delivery": f"s-{ctx}"},
        )
        assert st.json()["results"][0]["run_ids"] == [run_id]
    assert env.engine.get_run(run_id).step("ci").status == "running"
    ci = post_token(c, "/webhooks/ci", P.ci_completion(SHA, {"secret-scan": "success"}), P.CI_TOKEN)
    assert ci.status_code == 200
    run = env.engine.get_run(run_id)
    assert run.step("merge_gate").status == "succeeded" and run.step("preview").status == "running"

    # Argo CD: wrong revision does nothing, right one completes the run.
    post_token(c, "/webhooks/argocd", P.argocd_status("acme-preview", "9" * 40), P.ARGO_TOKEN)
    assert env.engine.get_run(run_id).status == "running"
    ar = post_token(c, "/webhooks/argocd", P.argocd_status("acme-preview", SHA), P.ARGO_TOKEN)
    assert ar.json()["results"][0]["run_ids"] == [run_id]
    assert env.engine.get_run(run_id).status == "succeeded"


def test_github_webhooks_use_sha256_prefix_and_check_runs(api: tuple[TestClient, Env]) -> None:
    c, env = api
    env.executor.script("pull_request", env.pr_script())
    run = env.start(__import__("arvino.domain", fromlist=["Profile"]).Profile.FAST)
    hdr = {"X-GitHub-Event": "check_run"}
    r = post_signed(
        c,
        "/webhooks/github",
        P.github_check_run(SHA, "unit-tests"),
        P.GITHUB_SECRET,
        "X-Hub-Signature-256",
        prefix="sha256=",
        extra=hdr,
    )
    assert r.status_code == 200 and r.json()["results"][0]["run_ids"] == [run.id]
    assert (
        post_signed(
            c,
            "/webhooks/github",
            P.github_check_run(SHA, "lint"),
            "wrong",
            "X-Hub-Signature-256",
            prefix="sha256=",
            extra=hdr,
        ).status_code
        == 401
    )


def test_pr_and_checks_for_foreign_repos_branches_or_commits_are_ignored(
    api: tuple[TestClient, Env],
) -> None:
    c, env = api
    env.executor.script("pull_request", env.pr_script())
    run = env.start(__import__("arvino.domain", fromlist=["Profile"]).Profile.FAST)
    hdr = {"X-Gitea-Event": "pull_request"}
    for payload in (
        P.git_pr("feature/not-arvino", "e" * 40),
        P.git_pr(f"arvino/{run.id}/pull_request", "e" * 40, repo="evil/fork"),
    ):
        r = post_signed(
            c, "/webhooks/gitea", payload, P.GITEA_SECRET, "X-Gitea-Signature", extra=hdr
        )
        assert r.json()["results"][0]["status"] == "ignored"
    assert env.engine.get_run(run.id).head_sha == SHA  # unchanged (set by the verified PR step)
    r = post_signed(
        c,
        "/webhooks/gitea",
        P.git_status("e" * 40, "unit-tests"),
        P.GITEA_SECRET,
        "X-Gitea-Signature",
        extra={"X-Gitea-Event": "status"},
    )
    assert r.json()["results"][0]["status"] == "ignored"
    r = post_signed(
        c,
        "/webhooks/gitea",
        P.git_status(SHA, "unit-tests", repo="evil/fork"),
        P.GITEA_SECRET,
        "X-Gitea-Signature",
        extra={"X-Gitea-Event": "status"},
    )
    assert r.json()["results"][0]["status"] == "ignored"


def test_agent_account_merge_detected_via_webhook() -> None:
    env = build_env(agent_accounts=frozenset({"arvino-bot"}))
    c = TestClient(create_app(env.engine, make_settings()))
    env.executor.script("pull_request", env.pr_script())
    run = env.start(__import__("arvino.domain", fromlist=["Profile"]).Profile.FAST)
    merged = P.git_pr(
        f"arvino/{run.id}/pull_request",
        SHA,
        action="closed",
        merged=True,
        merged_by="arvino-bot",
        merge_sha="f" * 40,
    )
    r = post_signed(
        c,
        "/webhooks/gitea",
        merged,
        P.GITEA_SECRET,
        "X-Gitea-Signature",
        extra={"X-Gitea-Event": "pull_request"},
    )
    assert r.status_code == 200
    got = env.engine.get_run(run.id)
    assert got.status == "failed" and "policy violation" in (got.failure_reason or "")


# ----------------------------------------------------------------------------- Nightingale


def test_nightingale_alert_creates_incident_issue_and_standard_run_once(
    api: tuple[TestClient, Env],
) -> None:
    c, env = api
    r1 = post_token(c, "/webhooks/nightingale", P.nightingale_alert(), P.N9E_TOKEN)
    assert r1.status_code == 200 and r1.json()["results"][0]["detail"] == "incident run created"
    r2 = post_token(
        c,
        "/webhooks/nightingale",
        P.nightingale_alert(),
        P.N9E_TOKEN,
        {"X-Arvino-Delivery": "other"},
    )
    assert r2.json()["results"][0]["detail"] == "incident run already exists"
    [run] = env.engine.list_runs()
    assert run.profile == "standard"
    issue = env.engine.get_issue(run.issue_id)
    assert (
        issue.kind == "incident"
        and issue.source == "nightingale"
        and "HighErrorRate" in issue.title
    )
    rec = post_token(c, "/webhooks/nightingale", P.nightingale_alert(recovered=True), P.N9E_TOKEN)
    assert "resolved" in rec.json()["results"][0]["detail"]
    assert env.engine.get_issue(run.issue_id).state == "resolved"
    assert len(env.engine.list_runs()) == 1


def test_nightingale_alert_for_unknown_project_is_ignored(api: tuple[TestClient, Env]) -> None:
    c, env = api
    r = post_token(c, "/webhooks/nightingale", P.nightingale_alert(project="nope"), P.N9E_TOKEN)
    assert r.json()["results"][0]["status"] == "ignored" and env.engine.list_runs() == []


def test_failed_processing_is_retryable_by_the_sender(
    api: tuple[TestClient, Env], monkeypatch: pytest.MonkeyPatch
) -> None:
    c, env = api
    calls = {"n": 0}
    orig = env.engine.record_deployment

    def flaky(*a: Any, **k: Any) -> list[str]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db hiccup")
        return orig(*a, **k)

    monkeypatch.setattr(env.engine, "record_deployment", flaky)
    h = {"X-Arvino-Delivery": "argo-1"}
    c2 = TestClient(c.app, raise_server_exceptions=False)
    assert (
        post_token(
            c2, "/webhooks/argocd", P.argocd_status("acme-preview", SHA), P.ARGO_TOKEN, h
        ).status_code
        == 500
    )
    assert (
        post_token(
            c2, "/webhooks/argocd", P.argocd_status("acme-preview", SHA), P.ARGO_TOKEN, h
        ).status_code
        == 200
    )
    assert (
        post_token(
            c2, "/webhooks/argocd", P.argocd_status("acme-preview", SHA), P.ARGO_TOKEN, h
        ).json()["status"]
        == "duplicate"
    )


def test_base_checks_constants_sane() -> None:
    assert set(BASE_CHECKS) < set(ALL_CHECKS) and REPO == "acme/app"
