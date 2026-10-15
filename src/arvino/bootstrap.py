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
    RecordingNotifier,
    RecordingTracker,
    RecordOnlyPromoter,
    SandboxExecutor,
    UnconfiguredGit,
)
from arvino.policy import FailClosedPolicy, OpaCliPolicy, OpaHttpPolicy
from arvino.ports import AgentExecutor, Decision, GitProvider, PolicyClient, Ports, RunNotifier
from arvino.settings import Settings, secret
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


def build_git_provider(settings: Settings) -> GitProvider:
    """Instantiate the configured Git provider for production mode.

    Returns ``GitHubProvider`` (default) or ``GiteaProvider`` based on
    ``ARVINO_GIT_PROVIDER``.  Falls back to ``UnconfiguredGit`` (fail-closed)
    when the required credentials are missing.
    """
    if settings.git_provider == "gitea":
        gitea_url = settings.gitea_url
        gitea_token = secret(settings.gitea_token)
        if gitea_url and gitea_token:
            from arvino.adapters.git_provider import GiteaProvider

            log.info("Git provider: Gitea at %s", gitea_url)
            return GiteaProvider(gitea_url, gitea_token)
        log.error(
            "ARVINO_GIT_PROVIDER=gitea but ARVINO_GITEA_URL / ARVINO_GITEA_TOKEN not set; "
            "Git operations will fail"
        )
        return UnconfiguredGit()

    # Default: GitHub via gh CLI (authentication handled by gh itself).
    from arvino.adapters.git_provider import GitHubProvider

    log.info("Git provider: GitHub (gh CLI)")
    return GitHubProvider()


def build_executor(settings: Settings) -> AgentExecutor:
    """Instantiate the agent executor for production (queue) mode.

    When ``ARVINO_REPO_BASE_DIR`` is set, ``ClaudeCodeExecutor`` runs Claude
    Code in isolated worktrees under that directory.  Otherwise the pull-model
    ``QueueExecutor`` is used (external harness picks up work items and posts
    completion via the API).
    """
    repo_base = Path(settings.repo_base_dir) if settings.repo_base_dir else None
    if repo_base is not None:
        from arvino.adapters.claude_code import ClaudeCodeExecutor

        log.info(
            "Agent executor: ClaudeCodeExecutor (model=%s, repo_base=%s)",
            settings.claude_model,
            repo_base,
        )
        return ClaudeCodeExecutor(
            repo_base_dir=repo_base,
            claude_bin=settings.claude_bin,
            model=settings.claude_model,
            max_turns=settings.claude_max_turns,
            timeout_seconds=settings.claude_timeout_seconds,
        )
    log.info("Agent executor: QueueExecutor (external harness)")
    return QueueExecutor()


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
        # Production mode: wire real adapters.
        git = build_git_provider(settings)
        executor = build_executor(settings)
        from arvino.adapters.role_router import RoleRouter

        executor = RoleRouter({"claude_code": executor})
    # Build the underlying notifier (Temporal or no-op) first.
    base_notifier = build_notifier(settings)
    # When Telegram is configured, wrap both tracker and notifier through it.
    tg_token = settings.tg_bot_token
    tg_chat = settings.tg_chat_id
    if tg_token and tg_chat:
        from arvino.adapters.telegram_notifier import TelegramRunNotifier

        log.info("TelegramRunNotifier: token configured, enabling Telegram status updates")
        tg = TelegramRunNotifier(tg_token, tg_chat, poke_fn=base_notifier.poke)
        ports = Ports(
            executor=executor,
            tracker=tg,
            git=git,
            promoter=RecordOnlyPromoter(),
            policy=policy,
            notifier=tg,
        )
    else:
        log.debug("TelegramRunNotifier: not configured, using RecordingTracker / base notifier")
        ports = Ports(
            executor=executor,
            tracker=RecordingTracker(),
            git=git,
            promoter=RecordOnlyPromoter(),
            policy=policy,
            notifier=base_notifier,
        )
    return Engine(
        make_session_factory(sa),
        load_registry(),
        ports,
        SystemClock(),
        EngineSettings(
            agent_accounts=frozenset(settings.agent_accounts),
            budget_usd=settings.run_cost_budget_usd,
        ),
    )
