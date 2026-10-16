"""Normalised inbound events. Parsers produce these; the dispatcher consumes them."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PROFILE_LABEL_PREFIX = "arvino:"


class IssueUpserted(BaseModel):
    source: Literal["plane", "github", "gitea"] = "plane"
    action: str
    external_id: str
    # Plane-originated issues carry a plane_project_id; GitHub/Gitea carry repo.
    plane_project_id: str | None = None
    repo: str | None = None  # "owner/repo" for GitHub/Gitea sources
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    state_group: str | None = None  # backlog | unstarted | started | completed | cancelled


class PullRequestUpdated(BaseModel):
    source: Literal["gitea", "github"]
    action: str
    repo: str
    number: int
    head_ref: str
    head_sha: str
    base_ref: str
    merged: bool = False
    merge_sha: str | None = None
    merged_by: str | None = None
    sender: str | None = None


class CheckCompleted(BaseModel):
    source: str
    repo: str
    sha: str
    name: str
    conclusion: Literal["success", "failure", "pending"]
    url: str | None = None
    branch: str | None = None


class DeploymentStatus(BaseModel):
    app: str
    revision: str
    health: str
    sync_status: str
    operation_phase: str | None = None


class AlertReceived(BaseModel):
    alert_id: str
    rule_name: str
    severity: int = Field(ge=1, le=3)
    recovered: bool
    target: str | None = None
    project_key: str | None = None
    note: str = ""


InboundEvent = (
    IssueUpserted | PullRequestUpdated | CheckCompleted | DeploymentStatus | AlertReceived
)
