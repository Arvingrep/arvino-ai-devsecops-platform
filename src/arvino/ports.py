"""Ports (interfaces) to external systems. The engine depends only on these.

Every port has a deterministic fake in :mod:`arvino.fakes`. Production adapters
must be exercised against the real system before being called "verified".
Note what is *absent*: :class:`GitProvider` has no ``merge`` method, so no code
path in this service can merge a pull request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from arvino.domain import ArtifactSpec, Principal


class IssueRef(BaseModel):
    id: str
    source: str
    external_id: str
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)


class StepRequest(BaseModel):
    run_id: str
    step_key: str
    role: str
    attempt: int
    idempotency_key: str  # f"{run_id}:{step_key}:{attempt}" - executors must dedupe on this
    branch_prefix: str  # agents may only write branches under this prefix
    actor: str  # identity the executor should act as (role-scoped)
    project_key: str
    repo: str | None
    issue: IssueRef
    upstream_artifacts: list[ArtifactSpec] = Field(default_factory=list)


class StepOutcome(BaseModel):
    """Result of executing a step. ``running`` means 'completion will arrive later'."""

    status: Literal["succeeded", "failed", "running"]
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactSpec] = Field(default_factory=list)
    error: str | None = None
    retryable: bool = True
    actor: str | None = None  # who actually did the work; defaults to the requested actor


class AgentExecutor(Protocol):
    def execute(self, request: StepRequest) -> StepOutcome: ...


class IssueTracker(Protocol):
    def post_status(self, issue: IssueRef, text: str) -> None: ...


class PullRequestInfo(BaseModel):
    number: int
    head_ref: str
    head_sha: str
    base_ref: str
    merged: bool = False


class GitProvider(Protocol):
    """Read + branch/PR-create operations only. There is deliberately no merge."""

    def push_branch(self, repo: str, branch: str, principal: Principal) -> None: ...

    def open_pull_request(
        self, repo: str, head: str, base: str, title: str, body: str, principal: Principal
    ) -> PullRequestInfo: ...

    def get_pull_request(self, repo: str, number: int) -> PullRequestInfo: ...

    def changed_paths(self, repo: str, number: int) -> list[str]: ...


class PromotionRequest(BaseModel):
    run_id: str
    project_key: str
    environment: str
    revision: str | None
    app: str | None  # Argo CD application name from project config, if known


class PromotionResult(BaseModel):
    reference: str  # e.g. GitOps PR URL or commit; never a direct cluster write
    detail: dict[str, Any] = Field(default_factory=dict)


class GitOpsPromoter(Protocol):
    def request_promotion(self, request: PromotionRequest) -> PromotionResult: ...


class Decision(BaseModel):
    allow: bool
    reasons: list[str] = Field(default_factory=list)


class PolicyClient(Protocol):
    def decide(self, policy_input: dict[str, Any]) -> Decision:
        """Must fail closed: any error is a denial, never an exception-as-allow."""
        ...


class RunNotifier(Protocol):
    """Tells the durable driver (Temporal) that a run's ledger changed."""

    def poke(self, run_id: str) -> None: ...


@dataclass
class Ports:
    """Bundle of adapters handed to the engine."""

    executor: AgentExecutor
    tracker: IssueTracker
    git: GitProvider
    promoter: GitOpsPromoter
    policy: PolicyClient
    notifier: RunNotifier
