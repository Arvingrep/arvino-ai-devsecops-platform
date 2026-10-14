"""Webhook payload builders. Shapes follow vendor documentation (NOT captured from live systems)."""

from __future__ import annotations

import json
from typing import Any

from arvino.webhooks.auth import sign

PLANE_SECRET = "plane-test-secret"
GITEA_SECRET = "gitea-test-secret"
GITHUB_SECRET = "github-test-secret"
CI_TOKEN = "ci-test-token"
ARGO_TOKEN = "argo-test-token"
N9E_TOKEN = "n9e-test-token"
OWNER_TOKEN = "owner-test-token"
AGENT_TOKEN = "agent-test-token"


def raw(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def plane_issue(
    project: str = "plane-proj-1",
    issue: str = "11111111-aaaa",
    labels: list[str] | None = None,
    action: str = "created",
    state_group: str = "unstarted",
) -> dict[str, Any]:
    return {
        "event": "issue",
        "action": action,
        "webhook_id": "wh-1",
        "workspace_id": "ws-1",
        "data": {
            "id": issue,
            "name": "Add widget endpoint",
            "description_stripped": "As a user I want a widget",
            "project": project,
            "state": {"id": "st-1", "name": "Todo", "group": state_group},
            "labels": [{"id": f"l{i}", "name": n} for i, n in enumerate(labels or [])],
        },
        "activity": {"actor": {"id": "u-1", "display_name": "arvin"}},
    }


def git_pr(
    run_branch: str,
    sha: str,
    number: int = 1,
    action: str = "opened",
    repo: str = "acme/app",
    merged: bool = False,
    merged_by: str | None = None,
    merge_sha: str | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number,
            "head": {"ref": run_branch, "sha": sha},
            "base": {"ref": "main"},
            "merged": merged,
            "merge_commit_sha": merge_sha,
            "merged_by": {"login": merged_by} if merged_by else None,
        },
        "repository": {"full_name": repo},
        "sender": {"login": "arvino-bot"},
    }


def git_status(
    sha: str, context: str, state: str = "success", repo: str = "acme/app"
) -> dict[str, Any]:
    return {
        "sha": sha,
        "state": state,
        "context": context,
        "target_url": f"https://ci.example/{context}/1",
        "repository": {"full_name": repo},
    }


def github_check_run(
    sha: str, name: str, conclusion: str = "success", repo: str = "acme/app"
) -> dict[str, Any]:
    return {
        "action": "completed",
        "check_run": {
            "name": name,
            "head_sha": sha,
            "conclusion": conclusion,
            "html_url": "https://gh/x",
        },
        "repository": {"full_name": repo},
    }


def ci_completion(
    sha: str, checks: dict[str, str], repo: str = "acme/app", branch: str | None = None
) -> dict[str, Any]:
    p: dict[str, Any] = {
        "repo": repo,
        "sha": sha,
        "checks": [
            {"name": n, "conclusion": c, "url": f"https://ci.example/{n}"}
            for n, c in checks.items()
        ],
    }
    if branch:
        p["branch"] = branch
    return p


def argocd_status(
    app: str, revision: str, health: str = "Healthy", sync: str = "Synced"
) -> dict[str, Any]:
    return {
        "app": app,
        "revision": revision,
        "health": health,
        "sync_status": sync,
        "operation_phase": "Succeeded",
    }


def nightingale_alert(
    alert_id: int = 4242, project: str = "acme", recovered: bool = False, severity: int = 1
) -> dict[str, Any]:
    return {
        "id": alert_id,
        "rule_id": 7,
        "rule_name": "HighErrorRate",
        "severity": severity,
        "is_recovered": recovered,
        "target_ident": "web-1",
        "trigger_time": 1_760_000_000,
        "tags": [f"project={project}", "env=production"],
        "rule_note": "5xx ratio above 5% for 5m",
    }


def hmac_headers(secret: str, body: bytes, header: str, prefix: str = "") -> dict[str, str]:
    return {header: prefix + sign(secret, body)}
