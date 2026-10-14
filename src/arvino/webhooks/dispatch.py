"""Maps normalised events onto engine actions. Handlers are idempotent; replay is harmless."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from arvino.db import SessionFactory, WebhookDeliveryRow, transaction
from arvino.domain import Profile
from arvino.engine import Engine
from arvino.views import ProjectView
from arvino.webhooks.events import (
    PROFILE_LABEL_PREFIX,
    AlertReceived,
    CheckCompleted,
    DeploymentStatus,
    InboundEvent,
    IssueUpserted,
    PullRequestUpdated,
)

log = logging.getLogger(__name__)

TERMINAL_STATE_GROUPS = {"completed", "cancelled"}
INCIDENT_PROFILE = Profile.STANDARD


class DispatchResult(BaseModel):
    status: str  # handled | ignored
    detail: str = ""
    run_ids: list[str] = []


class DeliveryLog:
    """Deduplicates deliveries by (source, delivery_id)."""

    def __init__(self, session_factory: SessionFactory, clock_now: Any) -> None:
        self._sf = session_factory
        self._now = clock_now

    def begin(self, source: str, delivery_id: str) -> bool:
        """True if this delivery should be processed; False if it was already processed."""
        try:
            with transaction(self._sf) as s:
                s.add(
                    WebhookDeliveryRow(
                        source=source,
                        delivery_id=delivery_id,
                        status="received",
                        received_at=self._now(),
                    )
                )
            return True
        except IntegrityError:
            with transaction(self._sf) as s:
                row = s.scalar(
                    select(WebhookDeliveryRow).where(
                        WebhookDeliveryRow.source == source,
                        WebhookDeliveryRow.delivery_id == delivery_id,
                    )
                )
                # failed/rejected/received deliveries may be retried by the sender; processed ones may not
                return bool(row and row.status != "processed")

    def finish(self, source: str, delivery_id: str, status: str, detail: str = "") -> None:
        with transaction(self._sf) as s:
            row = s.scalar(
                select(WebhookDeliveryRow).where(
                    WebhookDeliveryRow.source == source,
                    WebhookDeliveryRow.delivery_id == delivery_id,
                )
            )
            if row is not None:
                row.status, row.detail = status, detail[:500]

    def record_rejected(self, source: str, delivery_id: str, detail: str) -> None:
        self.begin(source, delivery_id)
        self.finish(source, delivery_id, "rejected", detail)


class Dispatcher:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def handle(self, event: InboundEvent) -> DispatchResult:
        if isinstance(event, IssueUpserted):
            return self._issue(event)
        if isinstance(event, PullRequestUpdated):
            return self._pull_request(event)
        if isinstance(event, CheckCompleted):
            return self._check(event)
        if isinstance(event, DeploymentStatus):
            return self._deployment(event)
        return self._alert(event)

    # -- Plane

    @staticmethod
    def _profile_from_labels(labels: list[str]) -> tuple[Profile | None, str | None]:
        """(profile, error). Exactly one valid ``arvino:<profile>`` label starts a run."""
        found: set[Profile] = set()
        for lab in labels:
            if lab.lower().startswith(PROFILE_LABEL_PREFIX):
                try:
                    found.add(Profile(lab.lower()[len(PROFILE_LABEL_PREFIX) :]))
                except ValueError:
                    return None, f"unknown profile label {lab!r}"
        if len(found) > 1:
            return None, "multiple arvino profile labels"
        return (next(iter(found)) if found else None), None

    def _issue(self, ev: IssueUpserted) -> DispatchResult:
        project = self.engine.find_project(plane_project_id=ev.plane_project_id)
        if project is None:
            return DispatchResult(status="ignored", detail="unknown Plane project")
        issue, _ = self.engine.upsert_issue(
            project.id,
            "plane",
            ev.external_id,
            ev.title,
            body=ev.body,
            labels=ev.labels,
            state=ev.state_group or "open",
        )
        profile, label_error = self._profile_from_labels(ev.labels)
        if label_error:
            return DispatchResult(status="ignored", detail=label_error)
        if profile is None or ev.action == "deleted" or ev.state_group in TERMINAL_STATE_GROUPS:
            return DispatchResult(status="handled", detail="issue stored; no run requested")
        run, created = self.engine.start_run(
            project.id,
            issue.id,
            profile,
            f"plane:{ev.external_id}:{profile.value}",
            "webhook:plane",
        )
        return DispatchResult(
            status="handled",
            detail="run created" if created else "run already exists",
            run_ids=[run.id],
        )

    # -- Git

    def _project_for_repo(self, repo: str) -> ProjectView | None:
        return self.engine.find_project(repo_full_name=repo)

    def _pull_request(self, ev: PullRequestUpdated) -> DispatchResult:
        run = self.engine.find_run_by_branch(ev.head_ref)
        project = self._project_for_repo(ev.repo)
        if run is None or project is None or run.project_id != project.id:
            return DispatchResult(
                status="ignored", detail="pull request does not belong to an Arvino run"
            )
        updates_head = ev.action in {
            "opened",
            "reopened",
            "synchronize",
            "synchronized",
            "edited",
            "closed",
        }
        self.engine.update_git_state(
            run.id,
            pr_number=ev.number,
            head_sha=ev.head_sha if updates_head else None,
            merged=ev.merged,
            merge_sha=ev.merge_sha,
            merged_by=ev.merged_by,
            source=ev.source,
        )
        return DispatchResult(status="handled", run_ids=[run.id])

    def _check(self, ev: CheckCompleted) -> DispatchResult:
        project = self._project_for_repo(ev.repo)
        if project is None:
            return DispatchResult(status="ignored", detail="unknown repository")
        runs = {
            r.id: r
            for r in self.engine.find_active_runs_by_head_sha(ev.sha)
            if r.project_id == project.id
        }
        if ev.branch:
            by_branch = self.engine.find_run_by_branch(ev.branch)
            if (
                by_branch is not None
                and by_branch.project_id == project.id
                and not _terminal(by_branch.status)
            ):
                runs[by_branch.id] = by_branch
        if not runs:
            return DispatchResult(status="ignored", detail="no active run for this commit")
        for run_id in runs:
            self.engine.record_check(
                run_id, ev.name, ev.sha, ev.conclusion, url=ev.url, source=ev.source
            )
        return DispatchResult(status="handled", run_ids=list(runs))

    # -- Argo CD

    def _deployment(self, ev: DeploymentStatus) -> DispatchResult:
        waiting = self.engine.record_deployment(
            ev.app, ev.revision, ev.health, ev.sync_status, ev.operation_phase
        )
        return DispatchResult(status="handled", detail="observation recorded", run_ids=waiting)

    # -- Nightingale

    def _alert(self, ev: AlertReceived) -> DispatchResult:
        projects = {p.key: p for p in self.engine.list_projects()}
        project = projects.get(ev.project_key or "")
        if project is None:
            return DispatchResult(status="ignored", detail="alert has no known project tag")
        title = f"[ALERT sev{ev.severity}] {ev.rule_name}" + (
            f" on {ev.target}" if ev.target else ""
        )
        issue, _ = self.engine.upsert_issue(
            project.id,
            "nightingale",
            ev.alert_id,
            title,
            body=ev.note,
            labels=["incident", f"sev{ev.severity}"],
            kind="incident",
            state="resolved" if ev.recovered else "open",
        )
        if ev.recovered:
            return DispatchResult(status="handled", detail="incident marked resolved; no new run")
        run, run_created = self.engine.start_run(
            project.id,
            issue.id,
            INCIDENT_PROFILE,
            f"nightingale:{ev.alert_id}",
            "webhook:nightingale",
        )
        return DispatchResult(
            status="handled",
            detail="incident run created" if run_created else "incident run already exists",
            run_ids=[run.id],
        )


def _terminal(status: str) -> bool:
    return status in {"succeeded", "failed", "cancelled"}
