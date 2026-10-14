"""Runtime configuration (environment variables, prefix ``ARVINO_``).

Secrets are :class:`SecretStr` and are never logged. Unset secrets disable the
corresponding capability (fail closed); they are never defaulted to a known value.
"""

from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
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

    # Webhook credentials. Unset -> that source is rejected.
    plane_webhook_secret: SecretStr | None = None
    gitea_webhook_secret: SecretStr | None = None
    github_webhook_secret: SecretStr | None = None
    ci_webhook_token: SecretStr | None = None
    argocd_webhook_token: SecretStr | None = None
    nightingale_webhook_token: SecretStr | None = None

    # Git accounts that must never merge (detective control; preventive is branch protection).
    agent_accounts: list[str] = []

    reconcile_interval_seconds: float = 30.0

    temporal_address: str | None = None
    temporal_namespace: str = "default"
    temporal_task_queue: str = "arvino-runs"


def secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value else None
