"""Domain vocabulary: enums, principals, value objects and errors.

Persistence lives in :mod:`arvino.db`; this module has no I/O.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Profile(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    GOVERNED = "governed"


class StepKind(StrEnum):
    AGENT = "agent"  # dispatched to an AgentExecutor
    CI = "ci"  # completes when trusted CI check results for the PR head are green
    GATE = "gate"  # deterministic policy evaluation on trusted inputs
    APPROVAL = "approval"  # blocks until a human owner decides
    DEPLOY = "deploy"  # GitOps promotion request, completes on Argo CD convergence


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_STEP_STATUSES


TERMINAL_STEP_STATUSES = frozenset({StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED})


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ApprovalKind(StrEnum):
    ARCHITECTURE = "architecture"  # architecture boundary / tech-stack / ADR acceptance
    PRODUCTION_DEPLOY = "production-deploy"
    DESTRUCTIVE_MIGRATION = "destructive-migration"
    TERRAFORM_APPLY = "terraform-apply"
    SPENDING = "spending"


class Role(StrEnum):
    TRIAGE = "triage"
    RESEARCHER = "researcher"
    PRODUCT = "product"
    ARCHITECT = "architect"
    DEVELOPER = "developer"
    FRONTEND = "frontend"
    BACKEND = "backend"
    QA = "qa"
    SECURITY = "security"
    IAC_REVIEW = "iac_review"
    REVIEWER = "reviewer"
    SRE = "sre"


IMPLEMENTER_ROLES = frozenset({Role.DEVELOPER, Role.FRONTEND, Role.BACKEND})
REVIEW_ROLES = frozenset({Role.QA, Role.SECURITY, Role.IAC_REVIEW, Role.REVIEWER})

ENVIRONMENTS = ("preview", "staging", "production")

BRANCH_PREFIX = "arvino"


def run_branch_prefix(run_id: str) -> str:
    return f"{BRANCH_PREFIX}/{run_id}/"


def default_actor(role: str) -> str:
    """Role-scoped agent identity; distinct roles never share an identity by default."""
    return f"agent:{role}"


class PrincipalKind(StrEnum):
    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: PrincipalKind
    roles: tuple[str, ...] = ()

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def as_policy_input(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind.value, "roles": list(self.roles)}


SYSTEM_PRINCIPAL = Principal(id="system:engine", kind=PrincipalKind.SYSTEM, roles=("orchestrator",))


class BmadArtifactKind(StrEnum):
    """Typed artifact kind tokens for BMAD v6.12 planning and implementation outputs.

    Registered in the run ledger when a product or architect step completes so
    that downstream steps can reference them via ``StepRequest.upstream_artifacts``.

    URI convention: ``git://<repo_full_name>/<git_ref>/<path_in_repo>``
    (e.g. ``git://Arvingrep/firebird-sea/main/_bmad-output/planning-artifacts/prds/prd.md``).
    """

    PRD = "bmad-prd"                    # Product Requirements Document
    ARCHITECTURE = "bmad-architecture"  # Architecture spine / ADR
    STORY = "bmad-story"                # Individual user story / acceptance criteria
    BRIEF = "bmad-brief"                # Project brief / discovery document


class ArtifactSpec(BaseModel):
    """An immutable pointer to a machine-readable artifact (Git object, report, ...)."""

    kind: str
    name: str
    uri: str
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrchestrationError(Exception):
    """Base class for domain errors that map to 4xx responses."""

    status_code = 400


class NotFoundError(OrchestrationError):
    status_code = 404


class ConflictError(OrchestrationError):
    status_code = 409


class PolicyDeniedError(OrchestrationError):
    status_code = 403

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons) or "denied by policy")
        self.reasons = reasons


class InvalidWorkflowError(OrchestrationError):
    status_code = 422
