"""Runtime configuration (environment variables, prefix ``ARVINO_``).

Secrets are :class:`SecretStr` and are never logged. Unset secrets disable the
corresponding capability (fail closed); they are never defaulted to a known value.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARVINO_", env_file=None, extra="ignore")

    database_url: str = "sqlite:///./arvino.db"

    # "queue": agent steps wait for an external harness (production-shaped).
    # "sandbox": in-process fakes that auto-complete; for local smoke tests ONLY.
    adapters: Literal["queue", "sandbox"] = "queue"

    # Policy backend. HTTP (OPA server) wins; otherwise the opa CLI + policy dir; otherwise deny-all.
    opa_url: str | None = None
    opa_bin: str | None = None
    policy_dir: str = "policy"

    # API credentials (bearer tokens). Unset -> that principal cannot authenticate.
    owner_token: SecretStr | None = None
    owner_id: str = "owner"
    agent_token: SecretStr | None = None
    automation_token: SecretStr | None = None

    # OIDC authentication (Authentik or any OIDC-compliant issuer).
    # When oidc_issuer is set, JWTs are accepted alongside static tokens.
    # jwks_url defaults to "{oidc_issuer}jwks/" when absent.
    oidc_issuer: str | None = None
    oidc_jwks_url: str | None = None
    oidc_audience: str | None = None
    oidc_role_claim: str = "arvino_role"

    # Webhook credentials. Unset -> that source is rejected.
    gitea_webhook_secret: SecretStr | None = None
    github_webhook_secret: SecretStr | None = None
    # Plane 集成已确定下线(2026-10-16决策,见PR#25全量移除),此字段暂留仅为
    # 兼容api.py里还没清理掉的plane webhook路由,不属于本PR范围,不新增功能。
    plane_webhook_secret: SecretStr | None = None
    ci_webhook_token: SecretStr | None = None
    argocd_webhook_token: SecretStr | None = None
    nightingale_webhook_token: SecretStr | None = None

    # Git provider selection: "github" uses gh CLI, "gitea" uses httpx REST.
    git_provider: Literal["github", "gitea"] = "github"

    # GitHub: personal access token (for API calls when gh CLI is not configured).
    github_token: SecretStr | None = None

    # Gitea: instance URL and token.
    gitea_url: str | None = None
    gitea_token: SecretStr | None = None

    # Claude Code executor settings.
    claude_model: str = "sonnet"
    claude_bin: str = "claude"
    claude_max_turns: int = 30
    claude_timeout_seconds: int = 600

    # Base directory containing the local clone that worktrees are created from.
    repo_base_dir: str | None = None

    # Git accounts that must never merge (detective control; preventive is branch protection).
    agent_accounts: list[str] = []

    reconcile_interval_seconds: float = 30.0

    temporal_address: str | None = None
    temporal_namespace: str = "default"
    temporal_task_queue: str = "arvino-runs"

    # Cost/budget control: when set, a run accumulating more than this amount in
    # Claude API costs will pause and require a spending-change approval before
    # any further agent steps are dispatched.  None = no limit enforced.
    run_cost_budget_usd: float | None = None

    # OpenHands Agent Canvas executor settings.
    # When openhands_base_url is set, OpenHandsExecutor is used instead of
    # QueueExecutor (external harness / agent-harness.sh).  The API key is read
    # from the OPENHANDS_API_KEY environment variable inside the executor itself
    # so it never surfaces through settings or logs.
    openhands_base_url: str | None = None
    openhands_timeout_seconds: int = 1800

    # Telegram notification adapter. When both are set, status updates and
    # approval pokes are sent to the configured Telegram chat.
    tg_bot_token: str | None = Field(default=None, validation_alias="ARVINO_TG_BOT_TOKEN")
    tg_chat_id: str | None = Field(default=None, validation_alias="ARVINO_TG_CHAT_ID")


def secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value else None
