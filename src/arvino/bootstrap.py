"""Wires settings into an Engine. The only place adapters are selected."""

from __future__ import annotations

import logging
from pathlib import Path

from arvino.clock import SystemClock
from arvino.db import init_schema, make_engine, make_session_factory
from arvino.engine import Engine, EngineSettings
from arvino.fakes import (
    FakeGit,
    QueueExecutor,
    RecordingTracker,
    RecordOnlyPromoter,
    SandboxExecutor,
    UnconfiguredGit,
)
from arvino.policy import FailClosedPolicy, OpaCliPolicy, OpaHttpPolicy
from arvino.ports import AgentExecutor, Decision, GitProvider, PolicyClient, Ports, RunNotifier
from arvino.settings import Settings
from arvino.workflows.model import load_registry

log = logging.getLogger(__name__)


class NoopNotifier:
    def poke(self, run_id: str) -> None:
        return None


class DenyAllPolicy:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    def decide(self, policy_input: dict[str, object]) -> Decision:
        return Decision(allow=False, reasons=[self._reason])


def build_policy(settings: Settings) -> PolicyClient:
    if settings.opa_url:
        return FailClosedPolicy(OpaHttpPolicy(settings.opa_url))
    try:
        return FailClosedPolicy(OpaCliPolicy(Path(settings.policy_dir), opa_bin=settings.opa_bin))
    except FileNotFoundError:
        log.error("no OPA backend configured (set ARVINO_OPA_URL); all policy decisions will DENY")
        return DenyAllPolicy("no policy backend configured")


def build_notifier(settings: Settings) -> RunNotifier:
    if settings.temporal_address:
        from arvino.temporal_worker import TemporalNotifier  # optional extra

        return TemporalNotifier(
            settings.temporal_address, settings.temporal_namespace, settings.temporal_task_queue
        )
    return NoopNotifier()


def build_engine(settings: Settings, *, init_db: bool = True) -> Engine:
    sa = make_engine(settings.database_url)
    if init_db:
        init_schema(sa)
    policy = build_policy(settings)
    git: GitProvider
    executor: AgentExecutor
    if settings.adapters == "sandbox":
        log.warning("ADAPTERS=sandbox: agent/Git/GitOps are in-process fakes. NOT FOR PRODUCTION.")
        fake = FakeGit()
        git, executor = fake, SandboxExecutor(fake)
    else:
        git, executor = UnconfiguredGit(), QueueExecutor()
    ports = Ports(
        executor=executor,
        tracker=RecordingTracker(),
        git=git,
        promoter=RecordOnlyPromoter(),
        policy=policy,
        notifier=build_notifier(settings),
    )
    return Engine(
        make_session_factory(sa),
        load_registry(),
        ports,
        SystemClock(),
        EngineSettings(agent_accounts=frozenset(settings.agent_accounts)),
    )
