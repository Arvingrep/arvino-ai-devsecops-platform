"""Pure payload parsers: (headers, body) -> normalised events.

Payload shapes follow the vendors' documented formats but were NOT captured from live
systems (see README *Verification status*). Unknown/invalid payloads raise
:class:`PayloadError` (422), they are never silently accepted.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from arvino.webhooks.events import (
    AlertReceived,
    CheckCompleted,
    DeploymentStatus,
    InboundEvent,
    IssueUpserted,
    PullRequestUpdated,
)


class PayloadError(Exception):
    """Payload is malformed or of an unsupported shape (maps to 422)."""


def _json(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise PayloadError("body is not valid JSON") from exc
    if not isinstance(data, dict):
        raise PayloadError("body must be a JSON object")
    return data


def _get(d: dict[str, Any], *path: str) -> Any:
    cur: Any = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            raise PayloadError(f"missing field {'.'.join(path)}")
        cur = cur[p]
    return cur


def normalise_conclusion(raw: str | None) -> str:
    """Map vendor states to success|failure|pending. Anything not clearly green is *not* success."""
    r = (raw or "").lower()
    if r == "success":
        return "success"
    if r in {"pending", "queued", "in_progress", "running", "waiting", "expected"}:
        return "pending"
    return "failure"  # failure/error/cancelled/timed_out/skipped/neutral/unknown


# ------------------------------------------------------------------------------------ Plane


def parse_plane(body: bytes) -> list[InboundEvent]:
    """Plane webhook: {"event": "issue", "action": "created|updated", "data": {...}}."""
    payload = _json(body)
    if payload.get("event") != "issue":
        return []  # other Plane events (cycle, project, ...) are not consumed
    action = str(_get(payload, "action"))
    data = _get(payload, "data")
    if not isinstance(data, dict):
        raise PayloadError("data must be an object")
    labels: list[str] = []
    for lab in data.get("labels") or []:
        name = lab.get("name") if isinstance(lab, dict) else lab
        if isinstance(name, str):
            labels.append(name)
    state = data.get("state")
    group = state.get("group") if isinstance(state, dict) else None
    try:
        return [
            IssueUpserted(
                action=action,
                external_id=str(_get(data, "id")),
                plane_project_id=str(_get(data, "project")),
                title=str(data.get("name") or ""),
                body=str(data.get("description_stripped") or data.get("description") or ""),
                labels=labels,
                state_group=group,
            )
        ]
    except ValidationError as exc:
        raise PayloadError(str(exc)) from exc


# ------------------------------------------------------------------------------------ Gitea / GitHub


def parse_git(provider: str, event: str | None, body: bytes) -> list[InboundEvent]:
    """Gitea or GitHub: pull_request, status, check_run events."""
    payload = _json(body)
    repo = str(_get(payload, "repository", "full_name"))
    src = "github" if provider == "github" else "gitea"
    if event == "issues":  # GitHub/Gitea issues.opened / issues.edited / issues.closed
        action = str(payload.get("action", ""))
        issue = _get(payload, "issue")
        if not isinstance(issue, dict):
            raise PayloadError("issue must be an object")
        labels: list[str] = []
        for lab in issue.get("labels") or []:
            name = lab.get("name") if isinstance(lab, dict) else lab
            if isinstance(name, str):
                labels.append(name)
        body_text = str(issue.get("body") or "")
        state = str(issue.get("state") or "open")
        # Map GitHub states to state_group vocab expected by dispatch
        state_group = "completed" if state == "closed" else "started"
        try:
            return [
                IssueUpserted(
                    source=src,
                    action=action,
                    external_id=str(_get(issue, "number")),
                    repo=repo,
                    title=str(issue.get("title") or ""),
                    body=body_text,
                    labels=labels,
                    state_group=state_group,
                )
            ]
        except ValidationError as exc:
            raise PayloadError(str(exc)) from exc
    if event == "pull_request":
        pr = _get(payload, "pull_request")
        merged_by = (pr.get("merged_by") or {}).get("login") if isinstance(pr, dict) else None
        sender = (payload.get("sender") or {}).get("login")
        try:
            return [
                PullRequestUpdated(
                    source=src,
                    action=str(_get(payload, "action")),
                    repo=repo,
                    number=int(_get(pr, "number")),
                    head_ref=str(_get(pr, "head", "ref")),
                    head_sha=str(_get(pr, "head", "sha")),
                    base_ref=str(_get(pr, "base", "ref")),
                    merged=bool(pr.get("merged", False)),
                    merge_sha=pr.get("merge_commit_sha"),
                    merged_by=merged_by,
                    sender=sender,
                )
            ]
        except (ValidationError, ValueError) as exc:
            raise PayloadError(str(exc)) from exc
    if event == "status":  # commit status API (Gitea + GitHub)
        return [
            CheckCompleted(
                source=src,
                repo=repo,
                sha=str(_get(payload, "sha")),
                name=str(_get(payload, "context")),
                conclusion=normalise_conclusion(payload.get("state")),
                url=payload.get("target_url"),
            )
        ]
    if event == "check_run":  # GitHub Actions / apps
        run = _get(payload, "check_run")
        conclusion = run.get("conclusion") if payload.get("action") == "completed" else "pending"
        return [
            CheckCompleted(
                source=src,
                repo=repo,
                sha=str(_get(run, "head_sha")),
                name=str(_get(run, "name")),
                conclusion=normalise_conclusion(conclusion),
                url=run.get("html_url"),
            )
        ]
    return []  # push, issue_comment, ... are acknowledged but not consumed


# ------------------------------------------------------------------------------------ CI completion


class _CheckIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    conclusion: str
    url: str | None = None


class _CiPayload(BaseModel):
    repo: str
    sha: str = Field(min_length=7, max_length=64)
    branch: str | None = None
    checks: list[_CheckIn] = Field(min_length=1)


def parse_ci(body: bytes) -> list[InboundEvent]:
    """CI completion contract: {"repo","sha","branch?","checks":[{"name","conclusion","url?"}]}."""
    try:
        p = _CiPayload.model_validate(_json(body))
    except ValidationError as exc:
        raise PayloadError(str(exc)) from exc
    return [
        CheckCompleted(
            source="ci",
            repo=p.repo,
            sha=p.sha,
            name=c.name,
            conclusion=normalise_conclusion(c.conclusion),
            url=c.url,
            branch=p.branch,
        )
        for c in p.checks
    ]


# ------------------------------------------------------------------------------------ Argo CD


class _ArgoPayload(BaseModel):
    app: str
    revision: str = Field(min_length=7)
    health: str
    sync_status: str
    operation_phase: str | None = None


def parse_argocd(body: bytes) -> list[InboundEvent]:
    """Argo CD notifications webhook contract (see docs/webhooks.md for the template)."""
    try:
        p = _ArgoPayload.model_validate(_json(body))
    except ValidationError as exc:
        raise PayloadError(str(exc)) from exc
    return [DeploymentStatus(**p.model_dump())]


# ------------------------------------------------------------------------------------ Nightingale


class _AlertPayload(BaseModel):
    id: int | str
    rule_name: str
    severity: int = Field(ge=1, le=3)
    is_recovered: bool = False
    target_ident: str | None = None
    tags: list[str] = Field(default_factory=list)
    rule_note: str = ""


def parse_nightingale(body: bytes) -> list[InboundEvent]:
    """Nightingale alert contract: n9e event fields (id, rule_name, severity, is_recovered, tags)."""
    try:
        p = _AlertPayload.model_validate(_json(body))
    except ValidationError as exc:
        raise PayloadError(str(exc)) from exc
    tags = dict(t.split("=", 1) for t in p.tags if "=" in t)
    return [
        AlertReceived(
            alert_id=str(p.id),
            rule_name=p.rule_name,
            severity=p.severity,
            recovered=p.is_recovered,
            target=p.target_ident,
            project_key=tags.get("project"),
            note=p.rule_note,
        )
    ]
