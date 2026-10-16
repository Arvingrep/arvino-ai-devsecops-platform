"""Read models returned by the engine and API (never ORM objects)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class _View(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectView(_View):
    id: str
    key: str
    name: str
    repo_full_name: str | None
    default_branch: str
    argocd_apps: dict[str, str]


class IssueView(_View):
    id: str
    project_id: str
    source: str
    external_id: str
    kind: str
    title: str
    body: str
    labels: list[str]
    state: str


class StepView(_View):
    key: str
    title: str
    kind: str
    role: str | None
    depends_on: list[str]
    environment: str | None
    approval_kind: str | None
    status: str
    attempt: int
    max_attempts: int
    timeout_seconds: int
    not_before: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    actor: str | None
    output: dict[str, Any]
    last_error: str | None
    cost_usd: float | None = None
    position: int = 0


class ApprovalView(_View):
    id: str
    run_id: str
    step_key: str
    kind: str
    status: str
    requested_at: datetime
    decided_at: datetime | None
    decided_by: dict[str, Any] | None
    reason: str | None


class ArtifactView(_View):
    run_id: str
    step_key: str
    kind: str
    name: str
    uri: str
    sha256: str | None
    produced_by: str
    extra: dict[str, Any]


class EventView(_View):
    id: int
    run_id: str | None
    type: str
    actor: str
    payload: dict[str, Any]
    created_at: datetime


class RunView(_View):
    id: str
    project_id: str
    issue_id: str
    profile: str
    definition_version: str
    status: str
    requested_by: str
    failure_reason: str | None
    pr_number: int | None
    head_sha: str | None
    merge_sha: str | None
    created_at: datetime
    updated_at: datetime
    cost_usd_total: float = 0.0
    steps: list[StepView] = []
    approvals: list[ApprovalView] = []
    artifacts: list[ArtifactView] = []
    next_wakeup_at: datetime | None = None

    def step(self, key: str) -> StepView:
        return next(s for s in self.steps if s.key == key)

    def statuses(self) -> dict[str, str]:
        return {s.key: s.status for s in self.steps}


class WorkItem(BaseModel):
    """A running agent step, as seen by a pull-model agent harness."""

    run_id: str
    step_key: str
    role: str
    attempt: int
    actor: str
    branch_prefix: str
    idempotency_key: str
