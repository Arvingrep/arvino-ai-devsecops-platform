"""Tests for bootstrap adapter wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from arvino.bootstrap import (
    DenyAllPolicy,
    NoopNotifier,
    build_engine,
    build_executor,
    build_git_provider,
    build_notifier,
    build_policy,
)
from arvino.engine import Engine
from arvino.fakes import QueueExecutor, SandboxExecutor, UnconfiguredGit
from arvino.settings import Settings

# --------------------------------------------------------------------------- #
# build_git_provider
# --------------------------------------------------------------------------- #


def test_build_git_provider_github_default() -> None:
    """Default git_provider='github' returns GitHubProvider."""
    settings = Settings(adapters="queue")
    git = build_git_provider(settings)
    from arvino.adapters.git_provider import GitHubProvider

    assert isinstance(git, GitHubProvider)


def test_build_git_provider_gitea_with_creds() -> None:
    """Gitea provider is instantiated when URL + token are set."""
    settings = Settings(
        adapters="queue",
        git_provider="gitea",
        gitea_url="https://gitea.example.com",
        gitea_token="tok-123",
    )
    git = build_git_provider(settings)
    from arvino.adapters.git_provider import GiteaProvider

    assert isinstance(git, GiteaProvider)
    git.close()


def test_build_git_provider_gitea_missing_creds() -> None:
    """Gitea without URL/token falls back to UnconfiguredGit (fail-closed)."""
    settings = Settings(adapters="queue", git_provider="gitea")
    git = build_git_provider(settings)
    assert isinstance(git, UnconfiguredGit)


# --------------------------------------------------------------------------- #
# build_executor
# --------------------------------------------------------------------------- #


def test_build_executor_queue_default() -> None:
    """Without repo_base_dir, executor is QueueExecutor (pull model)."""
    settings = Settings(adapters="queue")
    executor = build_executor(settings)
    assert isinstance(executor, QueueExecutor)


def test_build_executor_claude_code(tmp_path: Path) -> None:
    """With repo_base_dir, executor is ClaudeCodeExecutor."""
    settings = Settings(
        adapters="queue",
        repo_base_dir=str(tmp_path),
        claude_model="haiku",
        claude_max_turns=5,
        claude_timeout_seconds=120,
    )
    executor = build_executor(settings)
    from arvino.adapters.claude_code import ClaudeCodeExecutor

    assert isinstance(executor, ClaudeCodeExecutor)
    assert executor.model == "haiku"
    assert executor.max_turns == 5
    assert executor.timeout_seconds == 120
    assert executor.repo_base_dir == tmp_path


# --------------------------------------------------------------------------- #
# build_policy
# --------------------------------------------------------------------------- #


def test_build_policy_deny_all_when_no_opa() -> None:
    """Without OPA URL or binary on PATH, policy denies everything."""
    settings = Settings(adapters="queue", opa_url=None, opa_bin=None)
    # Only becomes DenyAllPolicy when opa binary is also absent from PATH.
    # When opa_bin is None and shutil.which("opa") returns None, FileNotFoundError
    # is raised in OpaCliPolicy.__init__ and caught by build_policy.
    with patch("arvino.policy.shutil.which", return_value=None):
        policy = build_policy(settings)
    assert isinstance(policy, DenyAllPolicy)


def test_build_policy_opa_http() -> None:
    """With OPA URL, returns FailClosedPolicy wrapping OpaHttpPolicy."""
    from arvino.policy import FailClosedPolicy, OpaHttpPolicy

    settings = Settings(adapters="queue", opa_url="http://opa:8181")
    policy = build_policy(settings)
    assert isinstance(policy, FailClosedPolicy)
    assert isinstance(policy._inner, OpaHttpPolicy)


# --------------------------------------------------------------------------- #
# build_notifier
# --------------------------------------------------------------------------- #


def test_build_notifier_noop_when_no_temporal() -> None:
    settings = Settings(adapters="queue", temporal_address=None)
    notifier = build_notifier(settings)
    assert isinstance(notifier, NoopNotifier)


# --------------------------------------------------------------------------- #
# build_engine end-to-end
# --------------------------------------------------------------------------- #


def test_build_engine_sandbox() -> None:
    """Sandbox mode wires fakes as before."""
    settings = Settings(adapters="sandbox", database_url="sqlite://")
    engine = build_engine(settings)
    assert isinstance(engine, Engine)
    assert isinstance(engine.ports.executor, SandboxExecutor)


def test_build_engine_queue_default() -> None:
    """Queue mode with defaults: QueueExecutor + GitHubProvider."""
    settings = Settings(adapters="queue", database_url="sqlite://")
    engine = build_engine(settings)
    assert isinstance(engine, Engine)
    from arvino.adapters.git_provider import GitHubProvider

    assert isinstance(engine.ports.git, GitHubProvider)
    from arvino.adapters.role_router import RoleRouter

    assert isinstance(engine.ports.executor, RoleRouter)
    assert isinstance(engine.ports.executor.adapters["claude_code"], QueueExecutor)


def test_build_engine_queue_claude_code(tmp_path: Path) -> None:
    """Queue mode with repo_base_dir: ClaudeCodeExecutor + GitHubProvider."""
    settings = Settings(
        adapters="queue",
        database_url="sqlite://",
        repo_base_dir=str(tmp_path),
        claude_model="opus",
    )
    engine = build_engine(settings)
    from arvino.adapters.claude_code import ClaudeCodeExecutor
    from arvino.adapters.git_provider import GitHubProvider
    from arvino.adapters.role_router import RoleRouter

    assert isinstance(engine.ports.executor, RoleRouter)
    inner = engine.ports.executor.adapters["claude_code"]
    assert isinstance(inner, ClaudeCodeExecutor)
    assert inner.model == "opus"
    assert isinstance(engine.ports.git, GitHubProvider)


def test_build_engine_queue_gitea(tmp_path: Path) -> None:
    """Queue mode with Gitea: GiteaProvider + ClaudeCodeExecutor."""
    settings = Settings(
        adapters="queue",
        database_url="sqlite://",
        git_provider="gitea",
        gitea_url="https://gitea.local",
        gitea_token="tok-abc",
        repo_base_dir=str(tmp_path),
    )
    engine = build_engine(settings)
    from arvino.adapters.claude_code import ClaudeCodeExecutor
    from arvino.adapters.git_provider import GiteaProvider
    from arvino.adapters.role_router import RoleRouter

    assert isinstance(engine.ports.git, GiteaProvider)
    assert isinstance(engine.ports.executor, RoleRouter)
    assert isinstance(engine.ports.executor.adapters["claude_code"], ClaudeCodeExecutor)
    engine.ports.git.close()


def test_settings_env_vars() -> None:
    """New settings are picked up from environment variables with ARVINO_ prefix."""
    env = {
        "ARVINO_GIT_PROVIDER": "gitea",
        "ARVINO_GITEA_URL": "https://git.co",
        "ARVINO_GITEA_TOKEN": "secret",
        "ARVINO_GITHUB_TOKEN": "ghp_xxx",
        "ARVINO_CLAUDE_MODEL": "opus",
        "ARVINO_CLAUDE_BIN": "/usr/local/bin/claude",
        "ARVINO_CLAUDE_MAX_TURNS": "50",
        "ARVINO_CLAUDE_TIMEOUT_SECONDS": "900",
        "ARVINO_REPO_BASE_DIR": "/repos",
    }
    with patch.dict("os.environ", env, clear=False):
        s = Settings(adapters="queue")  # type: ignore[call-arg]
        assert s.git_provider == "gitea"
        assert s.gitea_url == "https://git.co"
        assert s.claude_model == "opus"
        assert s.claude_bin == "/usr/local/bin/claude"
        assert s.claude_max_turns == 50
        assert s.claude_timeout_seconds == 900
        assert s.repo_base_dir == "/repos"
