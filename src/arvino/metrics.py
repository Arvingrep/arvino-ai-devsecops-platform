"""Prometheus metrics for the arvino orchestration service.

All metrics are query-based (pulled from the database) so they survive restarts
and are safe across multiple replicas. The ``/metrics`` endpoint calls
:func:`collect` with the current session factory.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from arvino.db import (
    ApprovalRow,
    EventRow,
    IssueRow,
    RunRow,
    SessionFactory,
    StepRow,
)

# -- registry and standard metrics ---------------------------------------------------

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "arvino_http_requests_total",
    "HTTP request count by method, path, and status",
    ["method", "path", "status"],
    registry=REGISTRY,
)

HTTP_LATENCY = Histogram(
    "arvino_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

# -- business metrics (populated from the database on scrape) -------------------------

RUNS_TOTAL = Gauge(
    "arvino_runs_total",
    "Total runs by profile, project, and status",
    ["profile", "project_id", "status"],
    registry=REGISTRY,
)

RUNS_ACTIVE = Gauge(
    "arvino_runs_active",
    "Active (non-terminal) runs",
    registry=REGISTRY,
)

STEPS_TOTAL = Gauge(
    "arvino_steps_total",
    "Total steps by kind, status, and role",
    ["kind", "status", "role"],
    registry=REGISTRY,
)

STEPS_ACTIVE = Gauge(
    "arvino_steps_active",
    "Steps currently running or waiting for approval",
    registry=REGISTRY,
)

APPROVALS_PENDING = Gauge(
    "arvino_approvals_pending",
    "Approvals awaiting human decision",
    ["kind"],
    registry=REGISTRY,
)

APPROVAL_WAIT_SECONDS = Histogram(
    "arvino_approval_wait_seconds",
    "Time from approval request to decision (for decided approvals)",
    ["kind", "decision"],
    buckets=(60, 300, 600, 1800, 3600, 7200, 14400, 43200, 86400),
    registry=REGISTRY,
)

STEP_DURATION_SECONDS = Histogram(
    "arvino_step_duration_seconds",
    "Step wall-clock duration (started_at -> finished_at)",
    ["kind", "status"],
    buckets=(10, 30, 60, 120, 300, 600, 1200, 1800, 3600),
    registry=REGISTRY,
)

ERRORS_TOTAL = Gauge(
    "arvino_errors_total",
    "Error events by type",
    ["type"],
    registry=REGISTRY,
)

ISSUES_OPEN = Gauge(
    "arvino_issues_open",
    "Open issues by kind and source",
    ["kind", "source"],
    registry=REGISTRY,
)

DB_UP = Gauge(
    "arvino_database_up",
    "1 if the database is reachable, 0 otherwise",
    registry=REGISTRY,
)

RUNS_STARTED_AT_SECONDS = Gauge(
    "arvino_runs_started_at_seconds",
    "Oldest created_at epoch for active runs, by status",
    ["status"],
    registry=REGISTRY,
)


# -- collection -----------------------------------------------------------------------


def collect(sf: SessionFactory) -> bytes:
    """Query the database and update all gauges, then return Prometheus text."""
    start = time.monotonic()
    with sf() as s:
        _collect_runs(s)
        _collect_steps(s)
        _collect_approvals(s)
        _collect_errors(s)
        _collect_issues(s)
        _collect_db_up(s)
    # Append collection duration
    Histogram(
        "arvino_metrics_collection_duration_seconds",
        "Time to collect metrics from the database",
        registry=REGISTRY,
    ).observe(time.monotonic() - start)
    return generate_latest(REGISTRY)


def _collect_runs(s: Session) -> None:
    rows = s.execute(
        select(RunRow.profile, RunRow.project_id, RunRow.status, func.count()).group_by(
            RunRow.profile, RunRow.project_id, RunRow.status
        )
    ).all()
    RUNS_TOTAL.clear()
    RUNS_ACTIVE.set(0)
    for row in rows:
        profile = row[0]
        project_id = row[1]
        status = row[2]
        count = row[3]
        RUNS_TOTAL.labels(profile=profile, project_id=project_id, status=status).set(count)
        if status in ("pending", "running", "waiting_approval"):
            RUNS_ACTIVE.inc(count)

    # Oldest created_at per active status for stuck-run alerting
    RUNS_STARTED_AT_SECONDS.clear()
    earliest_rows = s.execute(
        select(RunRow.status, func.min(RunRow.created_at))
        .where(RunRow.status.in_(["pending", "running", "waiting_approval"]))
        .group_by(RunRow.status)
    ).all()
    for row in earliest_rows:  # type: ignore[assignment]
        earliest_status = row[0]
        earliest: datetime = row[1]  # type: ignore[assignment]
        if earliest is not None:
            RUNS_STARTED_AT_SECONDS.labels(status=earliest_status).set(earliest.timestamp())


def _collect_steps(s: Session) -> None:
    rows = s.execute(
        select(StepRow.kind, StepRow.status, StepRow.role, func.count()).group_by(
            StepRow.kind, StepRow.status, StepRow.role
        )
    ).all()
    STEPS_TOTAL.clear()
    STEPS_ACTIVE.set(0)
    for row in rows:
        kind = row[0]
        status = row[1]
        role = row[2]
        count = row[3]
        role_label = role or "none"
        STEPS_TOTAL.labels(kind=kind, status=status, role=role_label).set(count)
        if status in ("running", "waiting_approval"):
            STEPS_ACTIVE.inc(count)

    # Step duration histogram: all terminal steps with both timestamps
    dur_rows = s.execute(
        select(StepRow.kind, StepRow.status, StepRow.started_at, StepRow.finished_at).where(
            StepRow.finished_at.isnot(None), StepRow.started_at.isnot(None)
        )
    ).all()
    for dur_row in dur_rows:
        kind = dur_row[0]
        status = dur_row[1]
        started = dur_row[2]
        finished = dur_row[3]
        if started is not None and finished is not None:
            STEP_DURATION_SECONDS.labels(kind=kind, status=status).observe(
                (finished - started).total_seconds()
            )


def _collect_approvals(s: Session) -> None:
    # Pending count by kind
    pending_rows = s.execute(
        select(ApprovalRow.kind, func.count())
        .where(ApprovalRow.status == "pending")
        .group_by(ApprovalRow.kind)
    ).all()
    APPROVALS_PENDING.clear()
    for row in pending_rows:
        kind = row[0]
        count = row[1]
        APPROVALS_PENDING.labels(kind=kind).set(count)

    # Wait time histogram for decided approvals
    decided_rows = s.execute(
        select(
            ApprovalRow.kind,
            ApprovalRow.status,
            ApprovalRow.requested_at,
            ApprovalRow.decided_at,
        ).where(ApprovalRow.decided_at.isnot(None), ApprovalRow.requested_at.isnot(None))
    ).all()
    for decided_row in decided_rows:
        kind = decided_row[0]
        decision = decided_row[1]
        requested = decided_row[2]
        decided = decided_row[3]
        if requested is not None and decided is not None:
            APPROVAL_WAIT_SECONDS.labels(kind=kind, decision=decision).observe(
                (decided - requested).total_seconds()
            )


def _collect_errors(s: Session) -> None:
    rows = s.execute(
        select(EventRow.type, func.count())
        .where(
            EventRow.type.in_(
                [
                    "run.failed",
                    "step.failed",
                    "step.stale_completion_ignored",
                    "approval.denied",
                    "approval.rejected",
                    "policy.denied",
                    "webhook.failed",
                ]
            )
        )
        .group_by(EventRow.type)
    ).all()
    ERRORS_TOTAL.clear()
    for row in rows:
        event_type = row[0]
        count = row[1]
        ERRORS_TOTAL.labels(type=event_type).set(count)


def _collect_issues(s: Session) -> None:
    rows = s.execute(
        select(IssueRow.kind, IssueRow.source, func.count())
        .where(IssueRow.state == "open")
        .group_by(IssueRow.kind, IssueRow.source)
    ).all()
    ISSUES_OPEN.clear()
    for row in rows:
        kind = row[0]
        source = row[1]
        count = row[2]
        ISSUES_OPEN.labels(kind=kind, source=source).set(count)


def _collect_db_up(s: Session) -> None:
    try:
        s.execute(text("SELECT 1"))
        DB_UP.set(1)
    except Exception:
        DB_UP.set(0)


# -- FastAPI middleware ---------------------------------------------------------------


class MetricsMiddleware:
    """ASGI middleware that records HTTP request counts and latency.

    Must be added before the app's own routes so it wraps all requests.
    Paths are normalised to avoid cardinality explosions: ``/runs/{uuid}``
    becomes ``/runs/*``.
    """

    SKIP_PATHS = frozenset({"/metrics", "/healthz", "/readyz"})

    def __init__(self, app: Callable[..., Any]) -> None:
        self._app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Any],
        send: Callable[..., Any],
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        path = scope.get("path", "/")
        method = scope.get("method", "UNKNOWN")

        if path in self.SKIP_PATHS:
            await self._app(scope, receive, send)
            return

        # Normalise path: replace UUID-like segments and numeric IDs with *
        normalised = _normalise_path(path)

        start = time.monotonic()
        status_code = 500

        async def _send(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 500)
            await send(message)

        try:
            await self._app(scope, receive, _send)
        finally:
            elapsed = time.monotonic() - start
            HTTP_REQUESTS.labels(method=method, path=normalised, status=str(status_code)).inc()
            HTTP_LATENCY.labels(method=method, path=normalised).observe(elapsed)


def _normalise_path(path: str) -> str:
    """Replace dynamic segments with ``*`` to keep metric cardinality low."""
    import re

    parts = path.strip("/").split("/")
    normalised: list[str] = []
    for p in parts:
        # UUIDs (36 chars, 8-4-4-4-12)
        if (
            re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", p)
            or re.fullmatch(r"[a-z]{3,4}-[0-9a-f]+", p)
            or p.isdigit()
        ):
            normalised.append("*")
        else:
            normalised.append(p)
    return "/" + "/".join(normalised) if normalised else "/"
