"""SQLAlchemy ledger. Portable across SQLite (tests/dev) and PostgreSQL (Compose/K8s)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.engine import Engine as SAEngine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from sqlalchemy.pool import StaticPool


class UTCDateTime(TypeDecorator[datetime]):
    """Always store/return timezone-aware UTC (SQLite drops tzinfo otherwise)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class ProjectRow(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    plane_project_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    repo_full_name: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    default_branch: Mapped[str] = mapped_column(String(100), default="main")
    argocd_apps: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class IssueRow(Base):
    __tablename__ = "issues"
    __table_args__ = (UniqueConstraint("project_id", "source", "external_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    source: Mapped[str] = mapped_column(String(32))  # plane | nightingale | api
    external_id: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32), default="task")  # task | incident
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text, default="")
    labels: Mapped[list[str]] = mapped_column(JSON, default=list)
    state: Mapped[str] = mapped_column(String(64), default="open")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class RunRow(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(300), unique=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    issue_id: Mapped[str] = mapped_column(ForeignKey("issues.id"))
    profile: Mapped[str] = mapped_column(String(16))
    definition_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    requested_by: Mapped[str] = mapped_column(String(200))
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Trusted pointers, written only from webhooks / the Git provider, never from agent output.
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    merge_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    steps: Mapped[list[StepRow]] = relationship(back_populates="run", order_by="StepRow.position")


class StepRow(Base):
    __tablename__ = "steps"
    __table_args__ = (UniqueConstraint("run_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    depends_on: Mapped[list[str]] = mapped_column(JSON, default=list)
    environment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    approval_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    required_checks: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    backoff_seconds: Mapped[int] = mapped_column(Integer)
    not_before: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    actor: Mapped[str | None] = mapped_column(String(200), nullable=True)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[RunRow] = relationship(back_populates="steps")


class ApprovalRow(Base):
    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("run_id", "step_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    step_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), index=True)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decided_by: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # Principal dump
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("run_id", "step_key", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    step_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    uri: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    produced_by: Mapped[str] = mapped_column(String(200))
    extra: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class CheckResultRow(Base):
    """Trusted CI check result for a specific commit; written only by webhook handlers."""

    __tablename__ = "check_results"
    __table_args__ = (UniqueConstraint("run_id", "name", "head_sha"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    head_sha: Mapped[str] = mapped_column(String(64))
    conclusion: Mapped[str] = mapped_column(String(16))  # success | failure | pending
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class DeploymentObservationRow(Base):
    """Latest Argo CD state seen per application (level-triggered deploy completion)."""

    __tablename__ = "deployment_observations"

    app: Mapped[str] = mapped_column(String(200), primary_key=True)
    revision: Mapped[str] = mapped_column(String(64))
    health: Mapped[str] = mapped_column(String(32))
    sync_status: Mapped[str] = mapped_column(String(32))
    operation_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)


class EventRow(Base):
    """Append-only audit log."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_runs.id"), index=True, nullable=True
    )
    type: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class WebhookDeliveryRow(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("source", "delivery_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))
    delivery_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16))  # received | processed | rejected | failed
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)


SessionFactory = Callable[[], Session]


def make_engine(url: str) -> SAEngine:
    if url.startswith("sqlite"):
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url == "sqlite://":
            kwargs["poolclass"] = StaticPool
        eng = create_engine(url, **kwargs)

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn: Any, _rec: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True)


def init_schema(engine: SAEngine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: SAEngine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def transaction(factory: SessionFactory) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
