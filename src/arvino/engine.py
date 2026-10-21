"""Orchestration engine: a level-triggered, idempotent state machine over the ledger.

Design rules (see ADR 0003/0004):

* The database is the source of truth. :meth:`Engine.advance` recomputes what is
  runnable from persisted state, so calling it repeatedly / concurrently / after a
  crash is safe.
* A step is *claimed* with a compare-and-set before dispatch, and every dispatch
  bumps ``attempt``. Completions carry the attempt number (fencing token); stale
  completions are recorded and ignored.
* Policy is consulted through the :class:`PolicyClient` port and fails closed. A
  minimal set of structural invariants is enforced here regardless of what
  policy says (policy may only make the system stricter).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from arvino.clock import Clock, SystemClock
from arvino.db import (
    ApprovalRow,
    ArtifactRow,
    CheckResultRow,
    DeploymentObservationRow,
    EventRow,
    IssueRow,
    ProjectRow,
    RepoWriteLockRow,
    RunRow,
    SessionFactory,
    StepRow,
    transaction,
)
from arvino.domain import (
    IMPLEMENTER_ROLES,
    REVIEW_ROLES,
    SYSTEM_PRINCIPAL,
    ApprovalKind,
    ApprovalStatus,
    ArtifactSpec,
    ConflictError,
    NotFoundError,
    PolicyDeniedError,
    Principal,
    PrincipalKind,
    Profile,
    Role,
    RunStatus,
    StepKind,
    StepStatus,
    default_actor,
    run_branch_prefix,
)
from arvino.ports import (
    IssueRef,
    Ports,
    PromotionRequest,
    PullRequestInfo,
    StepOutcome,
    StepRequest,
)
from arvino.views import (
    ApprovalView,
    ArtifactView,
    IssueView,
    LeaseView,
    ProjectView,
    RunView,
    StepView,
    WorkItem,
)
from arvino.workflows.model import WorkflowRegistry

log = logging.getLogger(__name__)

RUN_ID_RE = re.compile(r"^arvino/(run-[0-9a-f]{12})/")
_ERR_MAX = 2000

# External-executor leases.  TTLs are server-bounded: the client may ask for a
# shorter lease but can never extend it beyond LEASE_TTL_MAX_SECONDS.
LEASE_TTL_DEFAULT_SECONDS = 900
LEASE_TTL_MAX_SECONDS = 3600
# A lease can be renewed, but never past this hard deadline from first claim.
# Without it a healthy-but-wrong holder could pin a step (and repo lock) forever.
LEASE_MAX_LIFETIME_SECONDS = 4 * 3600
# Reclaim requires positive stop evidence; mere lease expiry is NOT evidence.
STOP_EVIDENCE_KINDS = frozenset({"terminated", "operator_declared"})
# Steps whose executor may write to the repository.  Read/evaluation roles
# (architect, qa, security, researcher, ...) deliberately do not take the lock.
REPO_WRITE_ROLES = frozenset(role.value for role in IMPLEMENTER_ROLES) | {
    "devops",
    "smartcontract",
}


class _LeaseConflict(ConflictError):
    """Conflict raised by a lease action, carrying a stable audit reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _hash_lease_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _repo_key(repo_full_name: str | None) -> str | None:
    """Normalise ``projects.repo_full_name`` for the write-lock primary key.

    Lower-casing is intentionally conservative: over-merging two spellings of
    the same repository is safer than under-merging and allowing concurrent
    writers.
    """
    if not repo_full_name:
        return None
    return repo_full_name.strip().lower() or None


class CompletionResult(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    STALE = "stale"
    EXPIRED = "expired"


@dataclass(frozen=True)
class EngineSettings:
    agent_accounts: frozenset[str] = field(default_factory=frozenset)
    max_advance_rounds: int = 50
    # When set, a spending approval is required once a run's cumulative API cost
    # exceeds this threshold. None means unlimited.
    budget_usd: float | None = None
    # Hard cap on a single lease's total lifetime, measured from first claim.
    lease_max_lifetime_seconds: int = LEASE_MAX_LIFETIME_SECONDS


def _failed(error: str, *, retryable: bool = False, **output: Any) -> StepOutcome:
    return StepOutcome(status="failed", error=error[:_ERR_MAX], retryable=retryable, output=output)


def run_id_from_branch(branch: str) -> str | None:
    m = RUN_ID_RE.match(branch)
    return m.group(1) if m else None


class Engine:
    def __init__(
        self,
        session_factory: SessionFactory,
        registry: WorkflowRegistry,
        ports: Ports,
        clock: Clock | None = None,
        settings: EngineSettings | None = None,
    ) -> None:
        self._sf = session_factory
        self.registry = registry
        self.ports = ports
        self.clock = clock or SystemClock()
        self.settings = settings or EngineSettings()

    # ------------------------------------------------------------------ helpers

    def _now(self) -> datetime:
        return self.clock.now()

    def _event(
        self, s: Session, run_id: str | None, type_: str, actor: str, **payload: Any
    ) -> None:
        s.add(
            EventRow(
                run_id=run_id, type=type_, actor=actor, payload=payload, created_at=self._now()
            )
        )

    def _lock_run(self, s: Session, run_id: str) -> RunRow:
        run = s.scalar(select(RunRow).where(RunRow.id == run_id).with_for_update())
        if run is None:
            raise NotFoundError(f"run {run_id} not found")
        return run

    @staticmethod
    def _step(run: RunRow, key: str) -> StepRow:
        for st in run.steps:
            if st.key == key:
                return st
        raise NotFoundError(f"step {key} not found in run {run.id}")

    # ------------------------------------------------------------------ projects / issues

    def create_project(
        self,
        key: str,
        name: str,
        *,
        plane_project_id: str | None = None,
        repo_full_name: str | None = None,
        default_branch: str = "main",
        argocd_apps: dict[str, str] | None = None,
    ) -> ProjectView:
        try:
            with transaction(self._sf) as s:
                row = ProjectRow(
                    id=f"prj-{uuid.uuid4().hex[:12]}",
                    key=key,
                    name=name,
                    plane_project_id=plane_project_id,
                    repo_full_name=repo_full_name,
                    default_branch=default_branch,
                    argocd_apps=argocd_apps or {},
                    created_at=self._now(),
                )
                s.add(row)
                s.flush()
                return ProjectView.model_validate(row)
        except IntegrityError as exc:
            raise ConflictError(f"project {key!r} conflicts with an existing project") from exc

    def get_project(self, project_id: str) -> ProjectView:
        with transaction(self._sf) as s:
            row = s.get(ProjectRow, project_id)
            if row is None:
                raise NotFoundError(f"project {project_id} not found")
            return ProjectView.model_validate(row)

    def list_projects(self) -> list[ProjectView]:
        with transaction(self._sf) as s:
            return [
                ProjectView.model_validate(p)
                for p in s.scalars(select(ProjectRow).order_by(ProjectRow.key))
            ]

    def find_project(
        self, *, plane_project_id: str | None = None, repo_full_name: str | None = None
    ) -> ProjectView | None:
        with transaction(self._sf) as s:
            stmt = select(ProjectRow)
            if plane_project_id is not None:
                stmt = stmt.where(ProjectRow.plane_project_id == plane_project_id)
            elif repo_full_name is not None:
                stmt = stmt.where(ProjectRow.repo_full_name == repo_full_name)
            else:
                return None
            row = s.scalar(stmt)
            return ProjectView.model_validate(row) if row else None

    def upsert_issue(
        self,
        project_id: str,
        source: str,
        external_id: str,
        title: str,
        *,
        body: str = "",
        labels: list[str] | None = None,
        kind: str = "task",
        state: str = "open",
    ) -> tuple[IssueView, bool]:
        with transaction(self._sf) as s:
            if s.get(ProjectRow, project_id) is None:
                raise NotFoundError(f"project {project_id} not found")
            row = s.scalar(
                select(IssueRow).where(
                    IssueRow.project_id == project_id,
                    IssueRow.source == source,
                    IssueRow.external_id == external_id,
                )
            )
            now = self._now()
            created = row is None
            if row is None:
                row = IssueRow(
                    id=f"iss-{uuid.uuid4().hex[:12]}",
                    project_id=project_id,
                    source=source,
                    external_id=external_id,
                    kind=kind,
                    title=title,
                    body=body,
                    labels=labels or [],
                    state=state,
                    created_at=now,
                    updated_at=now,
                )
                s.add(row)
            else:
                row.title, row.body, row.labels, row.state = title, body, labels or [], state
                row.updated_at = now
            s.flush()
            return IssueView.model_validate(row), created

    def get_issue(self, issue_id: str) -> IssueView:
        with transaction(self._sf) as s:
            row = s.get(IssueRow, issue_id)
            if row is None:
                raise NotFoundError(f"issue {issue_id} not found")
            return IssueView.model_validate(row)

    def list_issues(self, project_id: str) -> list[IssueView]:
        with transaction(self._sf) as s:
            rows = s.scalars(select(IssueRow).where(IssueRow.project_id == project_id))
            return [IssueView.model_validate(r) for r in rows]

    def record_suggestion(
        self,
        project_id: str,
        title: str,
        body: str = "",
        phase: int | None = None,
        requested_by: str = "user",
    ) -> IssueView:
        """Record a user suggestion as a backlog issue (does not start a run).

        Phase labels: backlog:phase-1, backlog:phase-2, etc.
        If no phase is given, label is just 'backlog'.
        """
        labels = ["backlog"]
        if phase is not None:
            labels.append(f"backlog:phase-{phase}")
        issue, _ = self.upsert_issue(
            project_id,
            source="suggestion",
            external_id=f"suggestion-{uuid.uuid4().hex[:12]}",
            title=title,
            body=body,
            labels=labels,
            kind="suggestion",
            state="backlog",
        )
        return issue

    # ------------------------------------------------------------------ runs

    def create_run(
        self,
        project_id: str,
        issue_id: str,
        profile: Profile,
        idempotency_key: str,
        requested_by: str,
    ) -> tuple[RunView, bool]:
        """Create a run (and its step rows) or return the existing one for the same key."""
        defn = self.registry.get(profile)
        try:
            with transaction(self._sf) as s:
                existing = s.scalar(select(RunRow).where(RunRow.idempotency_key == idempotency_key))
                if existing is not None:
                    if (
                        existing.project_id != project_id
                        or existing.issue_id != issue_id
                        or existing.profile != profile.value
                    ):
                        raise ConflictError(
                            "idempotency key already used with different parameters"
                        )
                    return self._run_view(s, existing), False
                issue = s.get(IssueRow, issue_id)
                if issue is None or s.get(ProjectRow, project_id) is None:
                    raise NotFoundError("project or issue not found")
                if issue.project_id != project_id:
                    raise ConflictError("issue does not belong to project")
                now = self._now()
                run = RunRow(
                    id=f"run-{uuid.uuid4().hex[:12]}",
                    idempotency_key=idempotency_key,
                    project_id=project_id,
                    issue_id=issue_id,
                    profile=profile.value,
                    definition_version=defn.version,
                    status=RunStatus.PENDING.value,
                    requested_by=requested_by,
                    created_at=now,
                    updated_at=now,
                )
                s.add(run)
                s.flush()  # no ORM relationship to events/approvals: order FK inserts explicitly
                for pos, sd in enumerate(defn.steps):
                    s.add(
                        StepRow(
                            run_id=run.id,
                            key=sd.key,
                            position=pos,
                            title=sd.title,
                            kind=sd.kind.value,
                            role=sd.role.value if sd.role else None,
                            depends_on=list(sd.depends_on),
                            environment=sd.environment,
                            approval_kind=sd.approval_kind.value if sd.approval_kind else None,
                            required_checks=list(sd.required_checks),
                            status=StepStatus.PENDING.value,
                            max_attempts=sd.max_attempts,
                            timeout_seconds=sd.timeout_seconds,
                            backoff_seconds=sd.backoff_seconds,
                        )
                    )
                self._event(
                    s,
                    run.id,
                    "run.created",
                    requested_by,
                    profile=profile.value,
                    definition=defn.version,
                )
                s.flush()
                s.refresh(run)
                return self._run_view(s, run), True
        except IntegrityError:
            # Lost a creation race on the idempotency key: return the winner.
            with transaction(self._sf) as s:
                winner = s.scalar(select(RunRow).where(RunRow.idempotency_key == idempotency_key))
                if winner is None:
                    raise
                return self._run_view(s, winner), False

    def start_run(
        self,
        project_id: str,
        issue_id: str,
        profile: Profile,
        idempotency_key: str,
        requested_by: str,
    ) -> tuple[RunView, bool]:
        view, created = self.create_run(
            project_id, issue_id, profile, idempotency_key, requested_by
        )
        before = RunStatus(view.status)
        view = self.advance(view.id)
        self._after_change(view.id, before)
        return self.get_run(view.id), created

    def get_run(self, run_id: str) -> RunView:
        with transaction(self._sf) as s:
            run = s.get(RunRow, run_id)
            if run is None:
                raise NotFoundError(f"run {run_id} not found")
            return self._run_view(s, run)

    def list_runs(
        self, *, active_only: bool = False, project_id: str | None = None
    ) -> list[RunView]:
        with transaction(self._sf) as s:
            stmt = select(RunRow).order_by(RunRow.created_at)
            if project_id:
                stmt = stmt.where(RunRow.project_id == project_id)
            runs = list(s.scalars(stmt))
            if active_only:
                runs = [r for r in runs if not RunStatus(r.status).terminal]
            return [self._run_view(s, r) for r in runs]

    def events(self, run_id: str) -> list[EventRow]:
        with transaction(self._sf) as s:
            rows = list(
                s.scalars(select(EventRow).where(EventRow.run_id == run_id).order_by(EventRow.id))
            )
            s.expunge_all()
            return rows

    def find_run_by_branch(self, branch: str) -> RunView | None:
        run_id = run_id_from_branch(branch)
        if run_id is None:
            return None
        try:
            return self.get_run(run_id)
        except NotFoundError:
            return None

    def find_active_runs_by_head_sha(self, sha: str) -> list[RunView]:
        with transaction(self._sf) as s:
            rows = list(s.scalars(select(RunRow).where(RunRow.head_sha == sha)))
            return [self._run_view(s, r) for r in rows if not RunStatus(r.status).terminal]

    def _run_view(self, s: Session, run: RunRow) -> RunView:
        approvals = list(
            s.scalars(
                select(ApprovalRow)
                .where(ApprovalRow.run_id == run.id)
                .order_by(ApprovalRow.requested_at)
            )
        )
        artifacts = list(
            s.scalars(
                select(ArtifactRow).where(ArtifactRow.run_id == run.id).order_by(ArtifactRow.id)
            )
        )
        view = RunView.model_validate(run)
        view.steps = [StepView.model_validate(st) for st in run.steps]
        view.approvals = [ApprovalView.model_validate(a) for a in approvals]
        view.artifacts = [ArtifactView.model_validate(a) for a in artifacts]
        wake: list[datetime] = []
        for st in run.steps:
            if st.status == StepStatus.PENDING.value and st.not_before:
                wake.append(st.not_before)
            if (
                st.status in (StepStatus.RUNNING.value, StepStatus.WAITING_APPROVAL.value)
                and st.started_at
            ):
                wake.append(st.started_at + timedelta(seconds=st.timeout_seconds))
        view.next_wakeup_at = min(wake) if wake and not RunStatus(run.status).terminal else None
        return view

    # ------------------------------------------------------------------ advance

    def advance(self, run_id: str) -> RunView:
        """Drive the run as far as it can go right now. Safe to call at any time, any number of times."""
        for _ in range(self.settings.max_advance_rounds):
            if not self._advance_once(run_id):
                break
        else:
            log.warning("advance(%s) hit max rounds", run_id)
        return self.get_run(run_id)

    def _advance_once(self, run_id: str) -> bool:
        claimed: list[tuple[str, int]] = []
        progressed = False
        with transaction(self._sf) as s:
            run = self._lock_run(s, run_id)
            if RunStatus(run.status).terminal:
                return False
            if run.status == RunStatus.PENDING.value:
                run.status = RunStatus.RUNNING.value
                run.updated_at = self._now()
                self._event(s, run.id, "run.started", "system:engine")
                progressed = True
            by_key = {st.key: st for st in run.steps}
            now = self._now()
            for st in run.steps:
                if st.status != StepStatus.PENDING.value:
                    continue
                if any(by_key[d].status != StepStatus.SUCCEEDED.value for d in st.depends_on):
                    continue
                if st.not_before is not None and st.not_before > now:
                    continue
                if st.kind == StepKind.APPROVAL.value:
                    if self._open_approval(s, run, st):
                        progressed = True
                    continue
                prev, new_attempt = st.attempt, st.attempt + 1
                res = s.execute(
                    update(StepRow)
                    .where(
                        StepRow.id == st.id,
                        StepRow.status == StepStatus.PENDING.value,
                        StepRow.attempt == prev,
                    )
                    .values(
                        status=StepStatus.RUNNING.value,
                        attempt=new_attempt,
                        started_at=now,
                        finished_at=None,
                        not_before=None,
                    )
                    .execution_options(synchronize_session=False)
                )
                if res.rowcount != 1:  # type: ignore[attr-defined]
                    continue  # another worker claimed it
                claimed.append((st.key, new_attempt))
                self._event(
                    s, run.id, "step.started", "system:engine", step=st.key, attempt=new_attempt
                )
            s.flush()
            s.expire_all()
            self._refresh_run_status(s, self._lock_run(s, run_id))

        for key, attempt in claimed:
            progressed = True
            outcome = self._dispatch(run_id, key, attempt)
            self._finalize(run_id, key, attempt, outcome, source="engine")
        if self._poll_running(run_id):
            progressed = True
        return progressed

    def _open_approval(self, s: Session, run: RunRow, st: StepRow) -> bool:
        now = self._now()
        st.status = StepStatus.WAITING_APPROVAL.value
        st.started_at = now
        assert st.approval_kind is not None
        exists = s.scalar(
            select(ApprovalRow).where(ApprovalRow.run_id == run.id, ApprovalRow.step_key == st.key)
        )
        if exists is None:
            s.add(
                ApprovalRow(
                    id=f"apr-{uuid.uuid4().hex[:12]}",
                    run_id=run.id,
                    step_key=st.key,
                    kind=st.approval_kind,
                    status=ApprovalStatus.PENDING.value,
                    requested_at=now,
                )
            )
        self._event(s, run.id, "approval.requested", "system:engine", step=st.key)
        return True

    # ------------------------------------------------------------------ dispatch

    def _dispatch(self, run_id: str, key: str, attempt: int) -> StepOutcome:
        with transaction(self._sf) as s:
            run = s.get(RunRow, run_id)
            assert run is not None
            st = self._step(run, key)
            kind, role, env = StepKind(st.kind), st.role, st.environment
            project = s.get(ProjectRow, run.project_id)
            issue = s.get(IssueRow, run.issue_id)
            assert project is not None and issue is not None
            upstream = self._upstream_artifacts(s, run, st)
            issue_ref = IssueRef(
                id=issue.id,
                source=issue.source,
                external_id=issue.external_id,
                title=issue.title,
                body=issue.body,
                labels=list(issue.labels),
            )
            repo, project_key = project.repo_full_name, project.key
            app = project.argocd_apps.get(env) if env else None
        try:
            if kind is StepKind.AGENT:
                assert role is not None
                req = StepRequest(
                    run_id=run_id,
                    step_key=key,
                    role=role,
                    attempt=attempt,
                    idempotency_key=f"{run_id}:{key}:{attempt}",
                    branch_prefix=run_branch_prefix(run_id),
                    actor=default_actor(role),
                    project_key=project_key,
                    repo=repo,
                    issue=issue_ref,
                    upstream_artifacts=upstream,
                )
                return self.ports.executor.execute(req)
            if kind is StepKind.GATE:
                return self._eval_gate(run_id, key)
            if kind is StepKind.CI:
                return self._eval_ci(run_id, key)
            if kind is StepKind.DEPLOY:
                assert env is not None
                return self._dispatch_deploy(run_id, key, env, app, project_key)
        except Exception as exc:
            log.warning("dispatch of %s/%s failed: %s", run_id, key, type(exc).__name__)
            return _failed(f"dispatch error: {type(exc).__name__}: {exc}", retryable=True)
        return _failed(f"cannot dispatch step kind {kind}")

    def _upstream_artifacts(self, s: Session, run: RunRow, st: StepRow) -> list[ArtifactSpec]:
        """Return artifacts from all transitive ancestor steps.

        Direct-dep-only filtering would silently drop BMAD artifacts produced by
        ``spec``/``adr`` steps whenever a non-agent step (APPROVAL, CI, GATE)
        sits between them and the consumer.  Passing all ancestor artifacts ensures
        every step sees the full BMAD context it needs, regardless of how many
        gating steps are in between.
        """
        by_key = {step.key: step for step in run.steps}
        # Compute transitive ancestor keys (same traversal as WorkflowDef.ancestors)
        ancestor_keys: set[str] = set()
        stack = list(st.depends_on)
        while stack:
            k = stack.pop()
            if k not in ancestor_keys:
                ancestor_keys.add(k)
                dep_step = by_key.get(k)
                if dep_step:
                    stack.extend(dep_step.depends_on)
        rows = s.scalars(
            select(ArtifactRow).where(ArtifactRow.run_id == run.id).order_by(ArtifactRow.id)
        )
        return [
            ArtifactSpec(kind=a.kind, name=a.name, uri=a.uri, sha256=a.sha256, metadata=a.extra)
            for a in rows
            if a.step_key in ancestor_keys
        ]

    # -- gate

    def _policy_run_context(self, s: Session, run: RunRow) -> dict[str, Any]:
        approvals = list(s.scalars(select(ApprovalRow).where(ApprovalRow.run_id == run.id)))
        deployed = [
            st.environment
            for st in run.steps
            if st.kind == StepKind.DEPLOY.value
            and st.status == StepStatus.SUCCEEDED.value
            and st.environment
        ]
        return {
            "id": run.id,
            "profile": run.profile,
            "actors": sorted({st.actor for st in run.steps if st.actor}),
            "approvals": [
                {"kind": a.kind, "status": a.status, "decided_by": a.decided_by} for a in approvals
            ],
            "deployed_envs": deployed,
        }

    @staticmethod
    def _checks_at_head(s: Session, run: RunRow) -> dict[str, str]:
        if run.head_sha is None:
            return {}
        rows = s.scalars(
            select(CheckResultRow).where(
                CheckResultRow.run_id == run.id, CheckResultRow.head_sha == run.head_sha
            )
        )
        return {r.name: r.conclusion for r in rows}

    def _eval_gate(self, run_id: str, key: str) -> StepOutcome:
        with transaction(self._sf) as s:
            run = s.get(RunRow, run_id)
            assert run is not None
            st = self._step(run, key)
            project = s.get(ProjectRow, run.project_id)
            assert project is not None
            checks = self._checks_at_head(s, run)
            required = list(st.required_checks)
            pr_number, head_sha, repo = run.pr_number, run.head_sha, project.repo_full_name
            ctx = self._policy_run_context(s, run)
        if repo is None or pr_number is None or head_sha is None:
            return _failed("gate needs a repository and a pull request with a known head commit")
        # Structural invariant (not delegated to policy): required checks are green at the *current* head.
        bad = [f"{c}={checks.get(c, 'missing')}" for c in required if checks.get(c) != "success"]
        if bad:
            return _failed(f"required checks not green at {head_sha[:12]}: {', '.join(bad)}")
        pr = self.ports.git.get_pull_request(
            repo, pr_number
        )  # provider errors -> retryable via _dispatch
        if pr.head_sha != head_sha or not pr.head_ref.startswith(run_branch_prefix(run_id)):
            return _failed("pull request head does not match the commit CI validated")
        paths = self.ports.git.changed_paths(repo, pr_number)
        decision = self.ports.policy.decide(
            {
                "action": "gate.evaluate",
                "principal": SYSTEM_PRINCIPAL.as_policy_input(),
                "run": ctx,
                "gate": {"changed_paths": paths, "required_checks": required, "checks": checks},
            }
        )
        if not decision.allow:
            return _failed("policy denied: " + "; ".join(decision.reasons), changed_paths=paths)
        return StepOutcome(
            status="succeeded", output={"changed_paths": paths, "head_sha": head_sha}
        )

    # -- ci

    def _eval_ci(self, run_id: str, key: str) -> StepOutcome:
        with transaction(self._sf) as s:
            run = s.get(RunRow, run_id)
            assert run is not None
            st = self._step(run, key)
            checks = self._checks_at_head(s, run)
            required, head = list(st.required_checks), run.head_sha
        if head is None:
            return StepOutcome(status="running", output={"waiting_for": "pull request head sha"})
        failed = [c for c in required if checks.get(c) == "failure"]
        if failed:
            return _failed(
                f"required check(s) failed at {head[:12]}: {', '.join(failed)}", head_sha=head
            )
        missing = [c for c in required if checks.get(c) != "success"]
        if missing:
            return StepOutcome(status="running", output={"waiting_for": missing, "head_sha": head})
        return StepOutcome(
            status="succeeded",
            output={"checks": {c: checks[c] for c in required}, "head_sha": head},
        )

    # -- deploy

    def _dispatch_deploy(
        self, run_id: str, key: str, env: str, app: str | None, project_key: str
    ) -> StepOutcome:
        with transaction(self._sf) as s:
            run = s.get(RunRow, run_id)
            assert run is not None
            checks = self._checks_at_head(s, run)
            ctx = self._policy_run_context(s, run)
            revision = run.head_sha if env == "preview" else run.merge_sha
            approved_prod = any(
                a["kind"] == ApprovalKind.PRODUCTION_DEPLOY.value
                and a["status"] == ApprovalStatus.APPROVED.value
                and (a["decided_by"] or {}).get("kind") == PrincipalKind.HUMAN.value
                and "owner" in (a["decided_by"] or {}).get("roles", [])
                for a in ctx["approvals"]
            )
        # Structural invariant (not delegated to policy): never dispatch production without an owner approval.
        if env == "production" and not approved_prod:
            return _failed("production deploy blocked: no approved production-deploy approval")
        if app is None:
            return _failed(f"project has no Argo CD application configured for {env}")
        baseline = ["unit-tests", "lint", "secret-scan"]
        decision = self.ports.policy.decide(
            {
                "action": "deploy.request",
                "principal": SYSTEM_PRINCIPAL.as_policy_input(),
                "run": ctx,
                "deploy": {
                    "environment": env,
                    "method": "gitops",
                    "required_checks": baseline,
                    "checks": checks,
                },
            }
        )
        if not decision.allow:
            return _failed("policy denied: " + "; ".join(decision.reasons))
        result = self.ports.promoter.request_promotion(
            PromotionRequest(
                run_id=run_id, project_key=project_key, environment=env, revision=revision, app=app
            )
        )
        return StepOutcome(
            status="running",
            output={
                "promotion": result.reference,
                "app": app,
                "expected_revision": revision,
                "environment": env,
            },
        )

    def _eval_deploy(self, s: Session, st: StepRow, run: RunRow) -> StepOutcome:
        app = st.output.get("app")
        expected = st.output.get("expected_revision")
        env = st.environment
        # The expected revision can become known after dispatch (human merges the PR later).
        if expected is None:
            expected = run.head_sha if env == "preview" else run.merge_sha
        if not app or not expected:
            return StepOutcome(status="running", output={"waiting_for": "revision"})
        obs = s.get(DeploymentObservationRow, app)
        if obs is None or obs.revision != expected:
            return StepOutcome(status="running", output={"expected_revision": expected})
        if obs.health == "Degraded" or obs.operation_phase in ("Failed", "Error"):
            return _failed(
                f"Argo CD app {app} unhealthy at {expected[:12]}: {obs.health}/{obs.operation_phase}"
            )
        if obs.health == "Healthy" and obs.sync_status == "Synced":
            return StepOutcome(
                status="succeeded", output={"app": app, "revision": expected, "health": obs.health}
            )
        return StepOutcome(status="running", output={"expected_revision": expected})

    # -- polling of externally-completed steps (level-triggered)

    def _poll_running(self, run_id: str) -> bool:
        with transaction(self._sf) as s:
            run = s.get(RunRow, run_id)
            assert run is not None
            if RunStatus(run.status).terminal:
                return False
            targets = [
                (st.key, st.attempt, StepKind(st.kind))
                for st in run.steps
                if st.status == StepStatus.RUNNING.value
                and st.kind in (StepKind.CI.value, StepKind.DEPLOY.value)
            ]
            results: list[tuple[str, int, StepOutcome]] = []
            for key, attempt, kind in targets:
                st = self._step(run, key)
                if kind is StepKind.DEPLOY:
                    out = self._eval_deploy(s, st, run)
                else:
                    out = StepOutcome(status="running")  # evaluated below, outside this txn
                results.append((key, attempt, out))
        progressed = False
        for key, attempt, out in results:
            kind = next(k for kk, _, k in targets if kk == key)
            if kind is StepKind.CI:
                out = self._eval_ci(run_id, key)
            if out.status != "running" and (
                self._finalize(run_id, key, attempt, out, source="engine")
                is CompletionResult.APPLIED
            ):
                progressed = True
        return progressed

    # ------------------------------------------------------------------ completion

    def complete_step(
        self,
        run_id: str,
        step_key: str,
        attempt: int,
        outcome: StepOutcome,
        principal: Principal,
        *,
        lease_token: str | None = None,
    ) -> CompletionResult:
        """External completion of an async step (agent harness).

        Idempotent and attempt-fenced.  When the step is lease-managed, the
        caller must present the plaintext lease token issued at claim time; the
        identity is always taken from ``principal``, never from ``outcome.actor``.
        """
        with transaction(self._sf) as s:
            run = s.get(RunRow, run_id)
            if run is None:
                raise NotFoundError(f"run {run_id} not found")
            st = self._step(run, step_key)
            if st.kind != StepKind.AGENT.value:
                raise ConflictError("only agent steps accept external completion")
            # Reject a bad lease before any read-only PR verification runs.
            lease_problem = self._lease_completion_problem(
                s, run_id, st, attempt, lease_token, principal.id, principal.id
            )
            if lease_problem is not None:
                return lease_problem
        before = self._run_status(run_id)
        result = self._finalize(
            run_id,
            step_key,
            attempt,
            outcome,
            source=principal.id,
            lease_token=lease_token,
            lease_principal=principal.id,
        )
        if result is CompletionResult.APPLIED:
            self.advance(run_id)
        self._after_change(run_id, before)
        return result

    def _finalize(
        self,
        run_id: str,
        key: str,
        attempt: int,
        outcome: StepOutcome,
        *,
        source: str,
        lease_token: str | None = None,
        lease_principal: str | None = None,
    ) -> CompletionResult:
        pr: PullRequestInfo | None = None
        if outcome.status == "succeeded":
            outcome, pr = self._preflight_success(run_id, key, outcome)
        return self._apply_outcome(
            run_id,
            key,
            attempt,
            outcome,
            source=source,
            verified_pr=pr,
            lease_token=lease_token,
            lease_principal=lease_principal,
        )

    def _preflight_success(
        self, run_id: str, key: str, outcome: StepOutcome
    ) -> tuple[StepOutcome, PullRequestInfo | None]:
        """Verify agent-claimed pull requests against the Git provider (trusted source)."""
        prs = [a for a in outcome.artifacts if a.kind == "pull-request"]
        if not prs:
            return outcome, None
        with transaction(self._sf) as s:
            run = s.get(RunRow, run_id)
            assert run is not None
            project = s.get(ProjectRow, run.project_id)
            assert project is not None
            repo, base = project.repo_full_name, project.default_branch
        number = prs[0].metadata.get("number")
        if repo is None or not isinstance(number, int):
            return _failed(
                "pull-request artifact needs metadata.number and a project repository"
            ), None
        try:
            info = self.ports.git.get_pull_request(repo, number)
        except Exception as exc:
            return _failed(
                f"cannot verify pull request: {type(exc).__name__}", retryable=True
            ), None
        if not info.head_ref.startswith(run_branch_prefix(run_id)):
            return _failed(
                f"pull request head {info.head_ref!r} is not under {run_branch_prefix(run_id)}"
            ), None
        if info.base_ref != base:
            return _failed(f"pull request base {info.base_ref!r} is not {base!r}"), None
        return outcome, info

    def _lease_completion_problem(
        self,
        s: Session,
        run_id: str,
        st: StepRow,
        attempt: int,
        lease_token: str | None,
        lease_principal: str | None,
        source: str,
    ) -> CompletionResult | None:
        """Validate a completion against the step lease, recording why it was rejected.

        Returns ``None`` when the step is not lease-managed or the completion is
        valid.  Identity comes from the authenticated Principal, never from the
        request body's ``actor`` field.
        """
        if st.executor_lease_token_hash is None:
            return None
        if lease_token is None:
            self._event(
                s,
                run_id,
                "step.lease_token_missing",
                source,
                step=st.key,
                attempt=attempt,
                executor=st.executor_principal,
            )
            return CompletionResult.STALE
        if lease_principal != st.executor_principal:
            self._event(
                s,
                run_id,
                "step.lease_principal_mismatch",
                source,
                step=st.key,
                attempt=attempt,
                executor=st.executor_principal,
                presented=lease_principal,
            )
            return CompletionResult.STALE
        if not hmac.compare_digest(st.executor_lease_token_hash, _hash_lease_token(lease_token)):
            self._event(
                s,
                run_id,
                "step.lease_token_mismatch",
                source,
                step=st.key,
                attempt=attempt,
                executor=st.executor_principal,
            )
            return CompletionResult.STALE
        if st.executor_attempt != st.attempt:
            self._event(
                s,
                run_id,
                "step.lease_attempt_mismatch",
                source,
                step=st.key,
                attempt=attempt,
                executor_attempt=st.executor_attempt,
                current_attempt=st.attempt,
            )
            return CompletionResult.STALE
        expires_at = st.executor_lease_expires_at
        if expires_at is None or expires_at <= self._now():
            self._event(
                s,
                run_id,
                "step.lease_expired_completion_ignored",
                source,
                step=st.key,
                attempt=attempt,
                executor=st.executor_principal,
                lease_expires_at=expires_at.isoformat() if expires_at else None,
            )
            return CompletionResult.EXPIRED
        return None

    def _apply_outcome(
        self,
        run_id: str,
        key: str,
        attempt: int,
        outcome: StepOutcome,
        *,
        source: str,
        verified_pr: PullRequestInfo | None = None,
        lease_token: str | None = None,
        lease_principal: str | None = None,
    ) -> CompletionResult:
        with transaction(self._sf) as s:
            run = self._lock_run(s, run_id)
            st = self._step(run, key)
            if (
                not (st.status == StepStatus.RUNNING.value and st.attempt == attempt)
                or RunStatus(run.status).terminal
            ):
                if (
                    st.status == StepStatus.SUCCEEDED.value
                    and st.attempt == attempt
                    and outcome.status == "succeeded"
                ):
                    return CompletionResult.DUPLICATE
                self._event(
                    s,
                    run_id,
                    "step.stale_completion_ignored",
                    source,
                    step=key,
                    attempt=attempt,
                    current_attempt=st.attempt,
                    status=st.status,
                )
                return CompletionResult.STALE

            lease_problem = self._lease_completion_problem(
                s, run_id, st, attempt, lease_token, lease_principal, source
            )
            if lease_problem is not None:
                return lease_problem

            if outcome.status == "running":
                if outcome.output:
                    st.output = {**st.output, **outcome.output}
                return CompletionResult.APPLIED

            # A non-running outcome consumes the lease: the external executor has
            # either finished or reported a failure.  The repo write lock is freed
            # with it.  Audit fields (principal/claimed_at/attempt) are retained.
            st.executor_lease_token_hash = None
            st.executor_lease_expires_at = None
            self._delete_repo_lock(s, run_id, key)

            actor = outcome.actor or (default_actor(st.role) if st.role else None)
            if outcome.status == "succeeded":
                problem = self._success_problem(s, run, st, actor, outcome)
                if problem:
                    outcome = _failed(problem)
            now = self._now()
            if outcome.status == "succeeded":
                self._register_artifacts(
                    s, run_id, key, outcome.artifacts, produced_by=actor or source
                )
                st.status = StepStatus.SUCCEEDED.value
                st.finished_at = now
                st.actor = actor if st.kind == StepKind.AGENT.value else st.actor
                st.output = {**st.output, **outcome.output}
                st.last_error = None
                if verified_pr is not None:
                    run.pr_number, run.head_sha = verified_pr.number, verified_pr.head_sha
                # Accumulate cost from agent steps.
                if st.kind == StepKind.AGENT.value:
                    raw_cost = outcome.output.get("cost_usd")
                    if isinstance(raw_cost, (int, float)) and raw_cost >= 0:
                        step_cost = float(raw_cost)
                        st.cost_usd = step_cost
                        run.cost_usd_total = (run.cost_usd_total or 0.0) + step_cost
                        self._event(
                            s,
                            run_id,
                            "step.cost_recorded",
                            source,
                            step=key,
                            cost_usd=step_cost,
                            run_cost_usd_total=run.cost_usd_total,
                        )
                        # Inject spending approval if budget exceeded and not already requested.
                        self._maybe_inject_spending_approval(s, run, now)
                self._event(
                    s, run_id, "step.succeeded", source, step=key, attempt=attempt, step_actor=actor
                )
            else:
                err = (outcome.error or "step failed")[:_ERR_MAX]
                st.last_error = err
                st.output = {**st.output, **outcome.output}
                if outcome.actor and st.kind == StepKind.AGENT.value:
                    st.actor = outcome.actor
                if outcome.retryable and st.attempt < st.max_attempts:
                    delay = st.backoff_seconds * (2 ** (st.attempt - 1))
                    st.status = StepStatus.PENDING.value
                    st.not_before = now + timedelta(seconds=delay)
                    self._event(
                        s,
                        run_id,
                        "step.retry_scheduled",
                        source,
                        step=key,
                        attempt=attempt,
                        error=err,
                        not_before=st.not_before.isoformat(),
                    )
                else:
                    st.status = StepStatus.FAILED.value
                    st.finished_at = now
                    self._event(
                        s, run_id, "step.failed", source, step=key, attempt=attempt, error=err
                    )
                    self._fail_run(s, run, f"step {key} failed: {err}")
            run.updated_at = now
            s.flush()
            self._refresh_run_status(s, run)
            return CompletionResult.APPLIED

    def _success_problem(
        self, s: Session, run: RunRow, st: StepRow, actor: str | None, outcome: StepOutcome
    ) -> str | None:
        """Invariants that turn an executor-reported success into a (non-retryable) failure."""
        if st.kind == StepKind.AGENT.value and st.role:
            role = Role(st.role)
            others = [o for o in run.steps if o.key != st.key and o.actor]
            if role in REVIEW_ROLES and any(
                o.role and Role(o.role) in IMPLEMENTER_ROLES and o.actor == actor for o in others
            ):
                return f"separation of duties: {actor!r} implemented this run and cannot act as {role.value}"
            if role in IMPLEMENTER_ROLES and any(
                o.role and Role(o.role) in REVIEW_ROLES and o.actor == actor for o in others
            ):
                return f"separation of duties: {actor!r} reviewed this run and cannot implement it"
        for spec in outcome.artifacts:
            existing = s.scalar(
                select(ArtifactRow).where(
                    ArtifactRow.run_id == run.id,
                    ArtifactRow.step_key == st.key,
                    ArtifactRow.name == spec.name,
                )
            )
            if existing and (existing.uri, existing.sha256, existing.kind) != (
                spec.uri,
                spec.sha256,
                spec.kind,
            ):
                return f"artifact {spec.name!r} already registered with different content (artifacts are immutable)"
        return None

    def _register_artifacts(
        self, s: Session, run_id: str, step_key: str, specs: list[ArtifactSpec], *, produced_by: str
    ) -> None:
        for spec in specs:
            exists = s.scalar(
                select(ArtifactRow).where(
                    ArtifactRow.run_id == run_id,
                    ArtifactRow.step_key == step_key,
                    ArtifactRow.name == spec.name,
                )
            )
            if exists is not None:
                continue  # identical (checked by _success_problem): idempotent re-registration
            s.add(
                ArtifactRow(
                    run_id=run_id,
                    step_key=step_key,
                    kind=spec.kind,
                    name=spec.name,
                    uri=spec.uri,
                    sha256=spec.sha256,
                    produced_by=produced_by,
                    extra=spec.metadata,
                    created_at=self._now(),
                )
            )

    # ------------------------------------------------------------------ run status

    def _refresh_run_status(self, s: Session, run: RunRow) -> None:
        if RunStatus(run.status).terminal:
            return
        statuses = {StepStatus(st.status) for st in run.steps}
        new = RunStatus(run.status)
        if statuses == {StepStatus.SUCCEEDED}:
            new = RunStatus.SUCCEEDED
        elif StepStatus.WAITING_APPROVAL in statuses and StepStatus.RUNNING not in statuses:
            new = RunStatus.WAITING_APPROVAL
        elif run.status != RunStatus.PENDING.value or statuses != {StepStatus.PENDING}:
            new = RunStatus.RUNNING
        if new.value != run.status:
            run.status = new.value
            run.updated_at = self._now()
            self._event(s, run.id, f"run.{new.value}", "system:engine")

    def _fail_run(
        self, s: Session, run: RunRow, reason: str, *, status: RunStatus = RunStatus.FAILED
    ) -> None:
        now = self._now()
        for st in run.steps:
            if st.status in (
                StepStatus.PENDING.value,
                StepStatus.RUNNING.value,
                StepStatus.WAITING_APPROVAL.value,
            ):
                st.status = StepStatus.CANCELLED.value
                st.finished_at = now
                # Cancellation terminates any external lease with the step.
                st.executor_lease_token_hash = None
                st.executor_lease_expires_at = None
        # A terminal run must never retain a repository write lock.
        s.execute(delete(RepoWriteLockRow).where(RepoWriteLockRow.run_id == run.id))
        for a in s.scalars(select(ApprovalRow).where(ApprovalRow.run_id == run.id)):
            if a.status == ApprovalStatus.PENDING.value:
                a.status = ApprovalStatus.CANCELLED.value
        run.status = status.value
        run.failure_reason = reason[:_ERR_MAX]
        run.updated_at = now
        self._event(s, run.id, f"run.{status.value}", "system:engine", reason=reason[:_ERR_MAX])

    def _run_status(self, run_id: str) -> RunStatus:
        return RunStatus(self.get_run(run_id).status)

    def _maybe_inject_spending_approval(self, s: Session, run: RunRow, now: datetime) -> None:
        """If the run's cumulative cost exceeds the configured budget and no spending
        approval has been requested yet, mark every still-pending agent step as blocked
        by inserting a synthetic spending-approval step ahead of them.

        Implementation: we add a new StepRow with kind=APPROVAL and approval_kind=SPENDING
        at the position just before the first pending agent step.  Because the engine
        is level-triggered, the approval will be opened by the next call to
        ``_advance_once`` without any special wiring.
        """
        budget = self.settings.budget_usd
        if budget is None:
            return
        total = run.cost_usd_total or 0.0
        if total <= budget:
            return

        # Check whether a spending approval already exists (pending OR decided) for this run.
        existing = s.scalar(
            select(ApprovalRow).where(
                ApprovalRow.run_id == run.id,
                ApprovalRow.kind == ApprovalKind.SPENDING.value,
            )
        )
        if existing is not None:
            return  # already raised once; don't keep inserting

        # Find the first pending agent step and insert a spending-approval step before it
        # (or after the last non-pending step if there is no pending agent step to guard).
        pending_agent_steps = [
            st
            for st in run.steps
            if st.kind == StepKind.AGENT.value and st.status == StepStatus.PENDING.value
        ]
        if not pending_agent_steps:
            return  # nothing pending to block; no point inserting

        # Choose the insertion position: just before the earliest pending agent step.
        insert_before_position = min(st.position for st in pending_agent_steps)

        # Shift all steps from that position onward to make room.
        for st in run.steps:
            if st.position >= insert_before_position:
                st.position += 1

        # Build the dependency list: the spending approval must depend on all steps
        # that the earliest pending agent step depends on (inherit its deps).
        first_pending = min(pending_agent_steps, key=lambda st: st.position)

        # The pending agent steps should also depend on this new approval.
        spending_key = "spending_approval"
        # Ensure uniqueness across retries / re-runs by appending a counter if the
        # key is somehow already taken.
        existing_keys = {st.key for st in run.steps}
        if spending_key in existing_keys:
            return  # already inserted (concurrent advance race)

        s.add(
            StepRow(
                run_id=run.id,
                key=spending_key,
                position=insert_before_position,
                title="Spending approval: run cost budget exceeded",
                kind=StepKind.APPROVAL.value,
                role=None,
                depends_on=list(first_pending.depends_on),
                environment=None,
                approval_kind=ApprovalKind.SPENDING.value,
                required_checks=[],
                status=StepStatus.PENDING.value,
                max_attempts=1,
                timeout_seconds=86400,  # 24 h to decide
                backoff_seconds=0,
            )
        )

        # All pending agent/ci/deploy steps at or after the insertion point must
        # depend on the spending approval so they don't start before it is decided.
        for st in run.steps:
            if (
                st.position > insert_before_position
                and st.status == StepStatus.PENDING.value
                and spending_key not in st.depends_on
            ):
                st.depends_on = [*list(st.depends_on), spending_key]

        self._event(
            s,
            run.id,
            "run.budget_exceeded",
            "system:engine",
            budget_usd=budget,
            run_cost_usd_total=total,
        )

    def _after_change(self, run_id: str, before: RunStatus) -> None:
        """Best-effort write-back to the issue tracker and poke of the durable driver."""
        view = self.get_run(run_id)
        if RunStatus(view.status) != before:
            try:
                issue = self.get_issue(view.issue_id)
                self.ports.tracker.post_status(
                    IssueRef(
                        id=issue.id,
                        source=issue.source,
                        external_id=issue.external_id,
                        title=issue.title,
                        body=issue.body,
                        labels=issue.labels,
                    ),
                    f"Arvino run {view.id} ({view.profile}) is {view.status}"
                    + (f": {view.failure_reason}" if view.failure_reason else ""),
                )
            except Exception as exc:
                log.warning("tracker write-back failed: %s", type(exc).__name__)
        try:
            self.ports.notifier.poke(run_id)
        except Exception as exc:
            log.warning("notifier poke failed: %s", type(exc).__name__)

    # ------------------------------------------------------------------ approvals

    def list_approvals(self, status: ApprovalStatus | None = None) -> list[ApprovalView]:
        with transaction(self._sf) as s:
            stmt = select(ApprovalRow).order_by(ApprovalRow.requested_at)
            if status:
                stmt = stmt.where(ApprovalRow.status == status.value)
            return [ApprovalView.model_validate(a) for a in s.scalars(stmt)]

    def decide_approval(
        self,
        approval_id: str,
        principal: Principal,
        decision: Literal["approved", "rejected"],
        reason: str | None = None,
    ) -> ApprovalView:
        # Phase 1: read state, check idempotency, build policy input.
        with transaction(self._sf) as s:
            appr = s.get(ApprovalRow, approval_id)
            if appr is None:
                raise NotFoundError(f"approval {approval_id} not found")
            if appr.status != ApprovalStatus.PENDING.value:
                prior = appr.decided_by or {}
                if appr.status == decision and prior.get("id") == principal.id:
                    return ApprovalView.model_validate(appr)  # idempotent replay
                raise ConflictError(f"approval already {appr.status}")
            run = s.get(RunRow, appr.run_id)
            assert run is not None
            ctx = self._policy_run_context(s, run)
            kind, run_id = appr.kind, appr.run_id

        denial: list[str] = []
        # Structural invariants, independent of policy.
        if principal.kind is not PrincipalKind.HUMAN:
            denial.append("only human principals may decide approvals")
        if not principal.has_role("owner"):
            denial.append("approver must hold the owner role")
        if principal.id in ctx["actors"]:
            denial.append("approver acted on this run (separation of duties)")
        verdict = self.ports.policy.decide(
            {
                "action": "approval.decide",
                "principal": principal.as_policy_input(),
                "run": ctx,
                "approval": {"kind": kind, "decision": decision},
            }
        )
        if not verdict.allow:
            denial.extend(r for r in verdict.reasons if r not in denial)
        if denial:
            with transaction(self._sf) as s:
                self._event(
                    s, run_id, "approval.denied", principal.id, approval=approval_id, reasons=denial
                )
            raise PolicyDeniedError(denial)

        # Phase 2: apply under the run lock, re-checking the approval is still pending.
        before = self._run_status(run_id)
        with transaction(self._sf) as s:
            run = self._lock_run(s, run_id)
            appr = s.get(ApprovalRow, approval_id)
            assert appr is not None
            if appr.status != ApprovalStatus.PENDING.value:
                raise ConflictError(f"approval already {appr.status}")
            st = self._step(run, appr.step_key)
            now = self._now()
            appr.status = decision
            appr.decided_at, appr.decided_by, appr.reason = (
                now,
                principal.model_dump(mode="json"),
                reason,
            )
            if decision == "approved":
                st.status, st.finished_at = StepStatus.SUCCEEDED.value, now
                self._event(
                    s, run_id, "approval.approved", principal.id, approval=approval_id, step=st.key
                )
                s.flush()
                self._refresh_run_status(s, run)
            else:
                st.status, st.finished_at, st.last_error = (
                    StepStatus.FAILED.value,
                    now,
                    f"rejected: {reason or ''}",
                )
                self._event(
                    s, run_id, "approval.rejected", principal.id, approval=approval_id, step=st.key
                )
                self._fail_run(s, run, f"approval {appr.kind} rejected by {principal.id}")
            s.flush()
            view = ApprovalView.model_validate(appr)
        self.advance(run_id)
        self._after_change(run_id, before)
        return view

    # ------------------------------------------------------------------ trusted inbound facts

    def record_check(
        self,
        run_id: str,
        name: str,
        head_sha: str,
        conclusion: Literal["success", "failure", "pending"],
        *,
        url: str | None = None,
        source: str = "ci",
    ) -> None:
        """Record a CI check result (called only from authenticated webhook handlers)."""
        before = self._run_status(run_id)
        with transaction(self._sf) as s:
            self._lock_run(s, run_id)
            row = s.scalar(
                select(CheckResultRow).where(
                    CheckResultRow.run_id == run_id,
                    CheckResultRow.name == name,
                    CheckResultRow.head_sha == head_sha,
                )
            )
            if row is None:
                s.add(
                    CheckResultRow(
                        run_id=run_id,
                        name=name,
                        head_sha=head_sha,
                        conclusion=conclusion,
                        url=url,
                        source=source,
                        updated_at=self._now(),
                    )
                )
            else:
                row.conclusion, row.url, row.source, row.updated_at = (
                    conclusion,
                    url,
                    source,
                    self._now(),
                )
            self._event(
                s, run_id, "check.recorded", source, check=name, sha=head_sha, conclusion=conclusion
            )
        self.advance(run_id)
        self._after_change(run_id, before)

    def update_git_state(
        self,
        run_id: str,
        *,
        pr_number: int | None = None,
        head_sha: str | None = None,
        merged: bool = False,
        merge_sha: str | None = None,
        merged_by: str | None = None,
        source: str = "git",
    ) -> None:
        before = self._run_status(run_id)
        with transaction(self._sf) as s:
            run = self._lock_run(s, run_id)
            if RunStatus(run.status).terminal:
                self._event(s, run_id, "git.event_ignored_terminal_run", source, head_sha=head_sha)
                return
            if pr_number is not None:
                run.pr_number = pr_number
            if head_sha is not None and head_sha != run.head_sha:
                if run.head_sha is not None:
                    self._event(s, run_id, "pr.head_moved", source, old=run.head_sha, new=head_sha)
                run.head_sha = head_sha
            if merged:
                if merged_by and merged_by in self.settings.agent_accounts:
                    self._event(s, run_id, "violation.agent_merge", source, merged_by=merged_by)
                    self._fail_run(
                        s,
                        run,
                        f"policy violation: agent account {merged_by!r} merged a pull request",
                    )
                    return_after = True
                else:
                    run.merge_sha = merge_sha
                    self._event(
                        s, run_id, "pr.merged", source, merged_by=merged_by, merge_sha=merge_sha
                    )
                    return_after = False
                if return_after:
                    self._after_change(run_id, before)
                    return
            run.updated_at = self._now()
        self.advance(run_id)
        self._after_change(run_id, before)

    def record_deployment(
        self,
        app: str,
        revision: str,
        health: str,
        sync_status: str,
        operation_phase: str | None = None,
    ) -> list[str]:
        """Record the latest Argo CD state for an app and advance runs waiting on it."""
        with transaction(self._sf) as s:
            row = s.get(DeploymentObservationRow, app)
            now = self._now()
            if row is None:
                s.add(
                    DeploymentObservationRow(
                        app=app,
                        revision=revision,
                        health=health,
                        sync_status=sync_status,
                        operation_phase=operation_phase,
                        observed_at=now,
                    )
                )
            else:
                row.revision, row.health, row.sync_status = revision, health, sync_status
                row.operation_phase, row.observed_at = operation_phase, now
            waiting = [
                r.id
                for r in s.scalars(select(RunRow))
                if not RunStatus(r.status).terminal
                and any(
                    st.kind == StepKind.DEPLOY.value
                    and st.status == StepStatus.RUNNING.value
                    and st.output.get("app") == app
                    for st in r.steps
                )
            ]
        for run_id in waiting:
            before = self._run_status(run_id)
            self.advance(run_id)
            self._after_change(run_id, before)
        return waiting

    # ------------------------------------------------------------------ lifecycle

    def cancel_run(self, run_id: str, principal: Principal, reason: str = "cancelled") -> RunView:
        if principal.kind is PrincipalKind.AGENT:
            raise PolicyDeniedError(["agents may not cancel runs"])
        before = self._run_status(run_id)
        with transaction(self._sf) as s:
            run = self._lock_run(s, run_id)
            if not RunStatus(run.status).terminal:
                self._fail_run(s, run, f"{reason} by {principal.id}", status=RunStatus.CANCELLED)
        self._after_change(run_id, before)
        return self.get_run(run_id)

    def reap_timeouts(self) -> list[tuple[str, str]]:
        """Fail (or retry) steps that exceeded their timeout. Returns (run_id, step_key) pairs."""
        now = self._now()
        expired: list[tuple[str, str, int, StepStatus]] = []
        with transaction(self._sf) as s:
            for run in s.scalars(select(RunRow)):
                if RunStatus(run.status).terminal:
                    continue
                for st in run.steps:
                    # A leased step is never retried by the timeout reaper: the old
                    # executor may still be alive.  An expired lease must be reclaimed
                    # by an operator with stop evidence first.
                    if (
                        st.status in (StepStatus.RUNNING.value, StepStatus.WAITING_APPROVAL.value)
                        and st.started_at is not None
                        and st.executor_lease_token_hash is None
                        and st.started_at + timedelta(seconds=st.timeout_seconds) <= now
                    ):
                        expired.append((run.id, st.key, st.attempt, StepStatus(st.status)))
        reaped: list[tuple[str, str]] = []
        for run_id, key, attempt, status in expired:
            before = self._run_status(run_id)
            if status is StepStatus.WAITING_APPROVAL:
                self._expire_approval(run_id, key)
                reaped.append((run_id, key))
            else:
                res = self._apply_outcome(
                    run_id,
                    key,
                    attempt,
                    _failed("timeout", retryable=True),
                    source="system:timeout",
                )
                if res is CompletionResult.APPLIED:
                    reaped.append((run_id, key))
            self.advance(run_id)
            self._after_change(run_id, before)
        return reaped

    def _expire_approval(self, run_id: str, key: str) -> None:
        with transaction(self._sf) as s:
            run = self._lock_run(s, run_id)
            st = self._step(run, key)
            if st.status != StepStatus.WAITING_APPROVAL.value:
                return
            appr = s.scalar(
                select(ApprovalRow).where(ApprovalRow.run_id == run_id, ApprovalRow.step_key == key)
            )
            if appr is not None and appr.status == ApprovalStatus.PENDING.value:
                appr.status = ApprovalStatus.EXPIRED.value
            st.status, st.finished_at, st.last_error = (
                StepStatus.FAILED.value,
                self._now(),
                "approval expired",
            )
            self._event(s, run_id, "approval.expired", "system:timeout", step=key)
            self._fail_run(s, run, f"approval for step {key} expired")

    def reconcile(self) -> list[str]:
        """Recovery entry point: reap timeouts, then advance every non-terminal run."""
        self.reap_timeouts()
        advanced: list[str] = []
        for run in self.list_runs(active_only=True):
            before = RunStatus(run.status)
            self.advance(run.id)
            self._after_change(run.id, before)
            advanced.append(run.id)
        return advanced

    # ------------------------------------------------------------------ pull-model agent support

    def list_work(self, role: str | None = None) -> list[WorkItem]:
        with transaction(self._sf) as s:
            items: list[WorkItem] = []
            for run in s.scalars(select(RunRow)):
                if RunStatus(run.status).terminal:
                    continue
                for st in run.steps:
                    if (
                        st.kind == StepKind.AGENT.value
                        and st.status == StepStatus.RUNNING.value
                        and st.role
                        # Claimed work is never offered again automatically, even
                        # after lease expiry: reclaim with stop evidence is required.
                        and st.executor_lease_token_hash is None
                    ):
                        if role and st.role != role:
                            continue
                        items.append(
                            WorkItem(
                                run_id=run.id,
                                step_key=st.key,
                                role=st.role,
                                attempt=st.attempt,
                                actor=default_actor(st.role),
                                branch_prefix=run_branch_prefix(run.id),
                                idempotency_key=f"{run.id}:{st.key}:{st.attempt}",
                            )
                        )
            return items

    # ------------------------------------------------------------------ external executor leases

    def _lease_ttl(self, ttl_seconds: int | None) -> int:
        if ttl_seconds is None:
            return LEASE_TTL_DEFAULT_SECONDS
        if ttl_seconds <= 0:
            raise ConflictError("ttl_seconds must be positive")
        return min(ttl_seconds, LEASE_TTL_MAX_SECONDS)

    def _lease_lifetime_cap(self, anchor: datetime) -> datetime:
        """Hard deadline for a lease that started at ``anchor``."""
        max_lifetime = self.settings.lease_max_lifetime_seconds
        if max_lifetime <= 0:
            raise ConflictError("lease_max_lifetime_seconds must be positive")
        return anchor + timedelta(seconds=max_lifetime)

    def _record_lease_rejection(
        self,
        run_id: str,
        step_key: str,
        principal_id: str,
        event_type: str,
        reason: str,
        detail: str,
    ) -> None:
        """Best-effort audit of a rejected lease action.

        The main transaction has already rolled back, so this uses a fresh one;
        failing to write the audit must not mask the original 4xx error.
        """
        try:
            with transaction(self._sf) as s:
                self._event(
                    s,
                    run_id,
                    event_type,
                    principal_id,
                    step=step_key,
                    reason=reason,
                    detail=detail[:500],
                )
        except Exception:  # pragma: no cover - defensive
            log.exception("failed to record %s for %s/%s", event_type, run_id, step_key)

    @staticmethod
    def _assert_claim_role(st: StepRow, principal: Principal) -> bool:
        """Authorise a claim by role and report whether the role was explicit.

        A Principal carrying roles must carry the step's role (``orchestrator`` is
        accepted as an operator override).  Legacy static agent tokens carry no
        role claims; they remain accepted for compatibility and the claim event
        records ``role_verified=False``.  The runtime permission model is a
        separate approval item (design §8.4); this method does not pretend to be
        that model.
        """
        if principal.has_role("orchestrator"):
            return True
        if not principal.roles:
            return False
        if st.role is None or not principal.has_role(st.role):
            raise PolicyDeniedError(
                [f"principal {principal.id!r} lacks role {st.role!r} for step {st.key!r}"]
            )
        return True

    @staticmethod
    def _delete_repo_lock(s: Session, run_id: str, step_key: str) -> None:
        s.execute(
            delete(RepoWriteLockRow).where(
                RepoWriteLockRow.run_id == run_id,
                RepoWriteLockRow.step_key == step_key,
            )
        )

    @staticmethod
    def _extend_repo_lock(
        s: Session,
        repo_key: str | None,
        run_id: str,
        step_key: str,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        if repo_key is None:
            return
        lock = s.get(RepoWriteLockRow, repo_key)
        if (
            lock is not None
            and lock.run_id == run_id
            and lock.step_key == step_key
            and hmac.compare_digest(lock.executor_lease_token_hash, token_hash)
        ):
            lock.expires_at = expires_at

    def claim_step(
        self,
        run_id: str,
        step_key: str,
        principal: Principal,
        *,
        ttl_seconds: int | None = None,
    ) -> LeaseView:
        """Claim a step, recording a rejection event when the claim loses."""
        try:
            return self._claim_step(run_id, step_key, principal, ttl_seconds=ttl_seconds)
        except _LeaseConflict as exc:
            self._record_lease_rejection(
                run_id, step_key, principal.id, "step.lease_claim_rejected", exc.reason, str(exc)
            )
            raise
        except PolicyDeniedError as exc:
            self._record_lease_rejection(
                run_id, step_key, principal.id, "step.lease_claim_rejected", "role_denied", str(exc)
            )
            raise

    def _claim_step(
        self,
        run_id: str,
        step_key: str,
        principal: Principal,
        *,
        ttl_seconds: int | None = None,
    ) -> LeaseView:
        """Atomically claim a running agent step for an external executor.

        The plaintext token is returned exactly once and only its SHA-256 is
        stored.  A lease is never stolen, not even after expiry: an operator must
        call :meth:`reclaim_lease` with stop evidence first.
        """
        ttl = self._lease_ttl(ttl_seconds)
        token = secrets.token_urlsafe(32)
        token_hash = _hash_lease_token(token)
        now = self._now()
        expires_at = min(
            now + timedelta(seconds=ttl), self._lease_lifetime_cap(now)
        )
        with transaction(self._sf) as s:
            run = self._lock_run(s, run_id)
            st = self._step(run, step_key)
            if st.kind != StepKind.AGENT.value:
                raise _LeaseConflict("not_claimable", "only agent steps can be claimed")
            if RunStatus(run.status).terminal:
                raise _LeaseConflict("not_claimable", f"run {run_id} is terminal")
            if st.status != StepStatus.RUNNING.value:
                raise _LeaseConflict(
                    "not_claimable",
                    f"step {step_key!r} is {st.status}; only running steps can be claimed",
                )
            if st.not_before is not None and st.not_before > now:
                raise _LeaseConflict(
                    "not_claimable", f"step {step_key!r} is not yet runnable"
                )

            if st.executor_lease_token_hash is not None:
                held_until = st.executor_lease_expires_at
                if held_until is not None and held_until <= now:
                    raise _LeaseConflict(
                        "lease_expired",
                        f"lease on {step_key!r} expired at {held_until.isoformat()}; "
                        "operator reclaim with stop evidence is required",
                    )
                suffix = f" until {held_until.isoformat()}" if held_until else ""
                raise _LeaseConflict(
                    "already_leased",
                    f"step {step_key!r} is already leased by {st.executor_principal!r}{suffix}",
                )

            role_verified = self._assert_claim_role(st, principal)
            project = s.get(ProjectRow, run.project_id)
            repo_key = _repo_key(project.repo_full_name if project else None)
            holds_repo = bool(repo_key and st.role in REPO_WRITE_ROLES)
            if holds_repo:
                assert repo_key is not None
                lock = s.get(RepoWriteLockRow, repo_key)
                if lock is not None:
                    if lock.expires_at <= now:
                        raise _LeaseConflict(
                            "repo_lock_expired",
                            f"repository {repo_key!r} is write-locked by "
                            f"{lock.executor_principal!r} until {lock.expires_at.isoformat()}; "
                            "operator reclaim is required",
                        )
                    raise _LeaseConflict(
                        "repo_locked",
                        f"repository {repo_key!r} is write-locked by "
                        f"{lock.executor_principal!r} (run {lock.run_id}, step {lock.step_key}) "
                        f"until {lock.expires_at.isoformat()}",
                    )
                s.add(
                    RepoWriteLockRow(
                        repo_key=repo_key,
                        run_id=run_id,
                        step_key=step_key,
                        executor_principal=principal.id,
                        executor_lease_token_hash=token_hash,
                        executor_attempt=st.attempt,
                        expires_at=expires_at,
                        acquired_at=now,
                    )
                )
                try:
                    s.flush()
                except IntegrityError as exc:
                    raise _LeaseConflict(
                        "repo_lock_race",
                        f"repository {repo_key!r} was write-locked concurrently",
                    ) from exc

            prev_attempt = st.attempt
            res = s.execute(
                update(StepRow)
                .where(
                    StepRow.id == st.id,
                    StepRow.status == StepStatus.RUNNING.value,
                    StepRow.attempt == prev_attempt,
                    StepRow.executor_lease_token_hash.is_(None),
                )
                .values(
                    executor_principal=principal.id,
                    executor_lease_expires_at=expires_at,
                    executor_lease_token_hash=token_hash,
                    executor_claimed_at=now,
                    executor_attempt=prev_attempt,
                )
                .execution_options(synchronize_session=False)
            )
            if res.rowcount != 1:  # type: ignore[attr-defined]
                raise _LeaseConflict(
                    "claim_race", f"step {step_key!r} was claimed concurrently"
                )
            self._event(
                s,
                run_id,
                "step.lease_claimed",
                principal.id,
                step=step_key,
                attempt=prev_attempt,
                expires_at=expires_at.isoformat(),
                repo_key=repo_key,
                holds_repo_lock=holds_repo,
                role_verified=role_verified,
            )
            return LeaseView(
                run_id=run_id,
                step_key=step_key,
                attempt=prev_attempt,
                executor_principal=principal.id,
                lease_expires_at=expires_at,
                repo_key=repo_key,
                role_verified=role_verified,
                lease_token=token,
            )

    def renew_lease(
        self,
        run_id: str,
        step_key: str,
        lease_token: str,
        principal: Principal,
        *,
        ttl_seconds: int | None = None,
    ) -> LeaseView:
        """Renew a lease, auditing a rejection that hits the lifetime cap."""
        try:
            return self._renew_lease(
                run_id, step_key, lease_token, principal, ttl_seconds=ttl_seconds
            )
        except _LeaseConflict as exc:
            self._record_lease_rejection(
                run_id, step_key, principal.id, "step.lease_renew_rejected", exc.reason, str(exc)
            )
            raise

    def _renew_lease(
        self,
        run_id: str,
        step_key: str,
        lease_token: str,
        principal: Principal,
        *,
        ttl_seconds: int | None = None,
    ) -> LeaseView:
        """Extend a live lease.  Expired leases cannot be renewed (reclaim first)."""
        ttl = self._lease_ttl(ttl_seconds)
        token_hash = _hash_lease_token(lease_token)
        now = self._now()
        with transaction(self._sf) as s:
            run = self._lock_run(s, run_id)
            st = self._step(run, step_key)
            if st.executor_lease_token_hash is None:
                raise ConflictError(f"step {step_key!r} has no active lease")
            if st.status != StepStatus.RUNNING.value:
                raise ConflictError(f"step {step_key!r} is {st.status}, not running")
            if principal.id != st.executor_principal:
                raise ConflictError("lease principal mismatch")
            if not hmac.compare_digest(st.executor_lease_token_hash, token_hash):
                raise ConflictError("lease token mismatch")
            if st.executor_attempt != st.attempt:
                raise ConflictError("lease attempt is stale")
            cap = self._lease_lifetime_cap(st.executor_claimed_at or now)
            if now >= cap:
                raise _LeaseConflict(
                    "max_lifetime_exceeded",
                    "lease reached its maximum lifetime; "
                    "operator reclaim with stop evidence is required",
                )
            if st.executor_lease_expires_at is None or st.executor_lease_expires_at <= now:
                raise ConflictError(
                    "lease expired; operator reclaim with stop evidence is required"
                )
            expires_at = min(now + timedelta(seconds=ttl), cap)
            res = s.execute(
                update(StepRow)
                .where(
                    StepRow.id == st.id,
                    StepRow.status == StepStatus.RUNNING.value,
                    StepRow.attempt == st.attempt,
                    StepRow.executor_lease_token_hash == token_hash,
                    # Never resurrect an expired lease, even under a local race.
                    StepRow.executor_lease_expires_at > now,
                )
                .values(executor_lease_expires_at=expires_at)
                .execution_options(synchronize_session=False)
            )
            if res.rowcount != 1:  # type: ignore[attr-defined]
                raise ConflictError("lease changed concurrently; renew rejected")
            project = s.get(ProjectRow, run.project_id)
            repo_key = _repo_key(project.repo_full_name if project else None)
            self._extend_repo_lock(s, repo_key, run_id, step_key, token_hash, expires_at)
            self._event(
                s,
                run_id,
                "step.lease_renewed",
                principal.id,
                step=step_key,
                attempt=st.attempt,
                expires_at=expires_at.isoformat(),
                repo_key=repo_key,
            )
            return LeaseView(
                run_id=run_id,
                step_key=step_key,
                attempt=st.attempt,
                executor_principal=principal.id,
                lease_expires_at=expires_at,
                repo_key=repo_key,
            )

    def release_lease(
        self,
        run_id: str,
        step_key: str,
        lease_token: str,
        principal: Principal,
    ) -> LeaseView:
        """Voluntarily release a lease (the strongest form of stop evidence)."""
        token_hash = _hash_lease_token(lease_token)
        with transaction(self._sf) as s:
            run = self._lock_run(s, run_id)
            st = self._step(run, step_key)
            if st.executor_lease_token_hash is None:
                raise ConflictError(f"step {step_key!r} has no active lease")
            if principal.id != st.executor_principal:
                raise ConflictError("lease principal mismatch")
            if not hmac.compare_digest(st.executor_lease_token_hash, token_hash):
                raise ConflictError("lease token mismatch")
            attempt = st.attempt
            res = s.execute(
                update(StepRow)
                .where(
                    StepRow.id == st.id,
                    StepRow.status == StepStatus.RUNNING.value,
                    StepRow.attempt == attempt,
                    StepRow.executor_lease_token_hash == token_hash,
                )
                .values(
                    status=StepStatus.PENDING.value,
                    not_before=None,
                    started_at=None,
                    executor_lease_token_hash=None,
                    executor_lease_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            if res.rowcount != 1:  # type: ignore[attr-defined]
                raise ConflictError("lease changed concurrently; release rejected")
            project = s.get(ProjectRow, run.project_id)
            repo_key = _repo_key(project.repo_full_name if project else None)
            self._delete_repo_lock(s, run_id, step_key)
            self._event(
                s,
                run_id,
                "step.lease_released",
                principal.id,
                step=step_key,
                attempt=attempt,
                repo_key=repo_key,
            )
            return LeaseView(
                run_id=run_id,
                step_key=step_key,
                attempt=attempt,
                executor_principal=principal.id,
                lease_expires_at=None,
                repo_key=repo_key,
            )

    def reclaim_lease(
        self,
        run_id: str,
        step_key: str,
        principal: Principal,
        *,
        stop_evidence: str,
        detail: str,
    ) -> LeaseView:
        """Reclaim an expired lease after positive evidence that the holder stopped.

        Lease expiry alone is not evidence.  Accepted evidence kinds are
        ``terminated`` (executor reported termination, grace elapsed, no renew)
        and ``operator_declared`` (an operator explicitly asserts the old
        executor is gone).  Both require a non-empty ``detail`` reference for
        the audit trail.
        """
        if principal.kind is PrincipalKind.AGENT or not (
            principal.has_role("owner") or principal.has_role("orchestrator")
        ):
            raise PolicyDeniedError(
                ["reclaim requires an operator principal (owner or orchestrator)"]
            )
        if stop_evidence not in STOP_EVIDENCE_KINDS:
            raise PolicyDeniedError(
                [
                    "reclaim requires stop evidence ('terminated' or "
                    "'operator_declared'); lease expiry alone is not evidence"
                ]
            )
        evidence_detail = detail.strip()
        if not evidence_detail:
            raise PolicyDeniedError(["reclaim requires a non-empty evidence detail/reference"])

        now = self._now()
        with transaction(self._sf) as s:
            run = self._lock_run(s, run_id)
            st = self._step(run, step_key)
            if st.executor_lease_token_hash is None:
                raise ConflictError(f"step {step_key!r} has no active lease")
            if st.status != StepStatus.RUNNING.value:
                raise ConflictError(f"step {step_key!r} is {st.status}, not running")
            if st.executor_attempt != st.attempt:
                raise ConflictError("lease attempt is stale")
            if st.executor_lease_expires_at is None or st.executor_lease_expires_at > now:
                raise ConflictError("lease is still valid; reclaim is only allowed after expiry")
            attempt = st.attempt
            prior_executor = st.executor_principal
            token_hash = st.executor_lease_token_hash
            res = s.execute(
                update(StepRow)
                .where(
                    StepRow.id == st.id,
                    StepRow.status == StepStatus.RUNNING.value,
                    StepRow.attempt == attempt,
                    StepRow.executor_lease_token_hash == token_hash,
                    # Do not reclaim a lease that was renewed concurrently.
                    StepRow.executor_lease_expires_at <= now,
                )
                .values(
                    status=StepStatus.PENDING.value,
                    not_before=None,
                    started_at=None,
                    executor_lease_token_hash=None,
                    executor_lease_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            if res.rowcount != 1:  # type: ignore[attr-defined]
                raise ConflictError("lease changed concurrently; reclaim rejected")
            project = s.get(ProjectRow, run.project_id)
            repo_key = _repo_key(project.repo_full_name if project else None)
            self._delete_repo_lock(s, run_id, step_key)
            self._event(
                s,
                run_id,
                "step.lease_reclaimed",
                principal.id,
                step=step_key,
                attempt=attempt,
                prior_executor=prior_executor,
                stop_evidence=stop_evidence,
                evidence_detail=evidence_detail,
                repo_key=repo_key,
            )
            return LeaseView(
                run_id=run_id,
                step_key=step_key,
                attempt=attempt,
                executor_principal=prior_executor or "",
                lease_expires_at=None,
                repo_key=repo_key,
            )
