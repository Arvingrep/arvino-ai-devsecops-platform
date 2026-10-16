"""Webhook payload builders. Base shapes loaded from real fixtures in tests/fixtures/webhooks/.

Factory functions accept overrides for test parametrisation; defaults match the fixture files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from arvino.webhooks.auth import sign

FIXTURES = Path(__file__).parent / "fixtures" / "webhooks"

PLANE_SECRET = "plane-test-secret"
GITEA_SECRET = "gitea-test-secret"
GITHUB_SECRET = "github-test-secret"
CI_TOKEN = "ci-test-token"
ARGO_TOKEN = "argo-test-token"
N9E_TOKEN = "n9e-test-token"
OWNER_TOKEN = "owner-test-token"
AGENT_TOKEN = "agent-test-token"


def _load_json(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_bytes())


def raw(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def plane_issue(
    project: str = "plane-proj-1",
    issue: str = "11111111-aaaa",
    labels: list[str] | None = None,
    action: str = "created",
    state_group: str = "unstarted",
) -> dict[str, Any]:
    base = _load_json("plane_issue_created.json")
    data = base["data"]
    data["id"] = issue
    data["project"] = project
    data["state"]["group"] = state_group
    if labels is not None:
        data["labels"] = [{"id": f"l{i}", "name": n} for i, n in enumerate(labels)]
    base["action"] = action
    return base


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
    base = _load_json("gitea_pr_opened.json")
    base["action"] = action
    base["number"] = number
    base["repository"]["full_name"] = repo
    pr = base["pull_request"]
    pr["number"] = number
    pr["head"]["ref"] = run_branch
    pr["head"]["sha"] = sha
    pr["merged"] = merged
    if merge_sha:
        pr["merge_commit_sha"] = merge_sha
    else:
        pr["merge_commit_sha"] = None
    if merged_by:
        pr["merged_by"] = {"login": merged_by}
    else:
        pr["merged_by"] = None
    return base


def git_status(
    sha: str, context: str, state: str = "success", repo: str = "acme/app"
) -> dict[str, Any]:
    base = _load_json("gitea_status_success.json")
    base["sha"] = sha
    base["context"] = context
    base["state"] = state
    base["repository"]["full_name"] = repo
    return base


def github_check_run(
    sha: str, name: str, conclusion: str = "success", repo: str = "acme/app"
) -> dict[str, Any]:
    base = _load_json("github_check_run_success.json")
    base["check_run"]["head_sha"] = sha
    base["check_run"]["name"] = name
    base["check_run"]["conclusion"] = conclusion
    base["repository"]["full_name"] = repo
    return base


def ci_completion(
    sha: str, checks: dict[str, str], repo: str = "acme/app", branch: str | None = None
) -> dict[str, Any]:
    base = _load_json("ci_completion_mixed.json")
    base["repo"] = repo
    base["sha"] = sha
    base["checks"] = [
        {"name": n, "conclusion": c, "url": f"https://ci.example/{n}"} for n, c in checks.items()
    ]
    if branch:
        base["branch"] = branch
    else:
        base.pop("branch", None)
    return base


def argocd_status(
    app: str, revision: str, health: str = "Healthy", sync: str = "Synced"
) -> dict[str, Any]:
    base = _load_json("argocd_status_healthy.json")
    base["app"] = app
    base["revision"] = revision
    base["health"] = health
    base["sync_status"] = sync
    return base


def nightingale_alert(
    alert_id: int = 4242, project: str = "acme", recovered: bool = False, severity: int = 1
) -> dict[str, Any]:
    base = _load_json("nightingale_alert_critical.json")
    base["id"] = alert_id
    base["severity"] = severity
    base["is_recovered"] = recovered
    base["tags"] = [f"project={project}", "env=production"]
    return base


def github_issue(
    number: int = 99,
    title: str = "Test issue",
    body: str = "body text",
    labels: list[str] | None = None,
    action: str = "opened",
    state: str = "open",
    repo: str = "Arvingrep/firebird-sea",
) -> dict[str, Any]:
    """Minimal GitHub issues webhook payload."""
    return {
        "action": action,
        "issue": {
            "number": number,
            "title": title,
            "body": body,
            "state": state,
            "labels": [{"id": i + 1, "name": n} for i, n in enumerate(labels or [])],
        },
        "repository": {"full_name": repo},
        "sender": {"login": "arvin"},
    }


def hmac_headers(secret: str, body: bytes, header: str, prefix: str = "") -> dict[str, str]:
    return {header: prefix + sign(secret, body)}
