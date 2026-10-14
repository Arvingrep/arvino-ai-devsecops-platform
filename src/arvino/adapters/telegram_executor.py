"""Telegram Bot + Claude Code adapter for the AgentExecutor port.

Posts step progress to the shared Arvino Telegram group, runs Claude Code in an
isolated worktree, and posts results back.  Each workflow role uses its own bot
token (mapping from bot-role-mapping.md).

Owner approvals are handled by polling the Telegram group for ``/approve`` and
``/reject`` commands from the owner (identified by ``owner_telegram_id``).

The adapter is synchronous so it satisfies the :class:`AgentExecutor` protocol.
Telegram API calls are made via ``httpx`` (already a project dependency).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from arvino.domain import ArtifactSpec
from arvino.ports import StepOutcome, StepRequest

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"
_DEFAULT_MODEL = "sonnet"
_DEFAULT_MAX_TURNS = 30
_DEFAULT_ALLOWED_TOOLS = "Read,Write,Edit,Bash"
_DEFAULT_APPROVAL_POLL_SECONDS = 300  # 5 minutes for owner to approve
_DEFAULT_POLL_INTERVAL = 3.0

# Workflow role → environment variable key for the bot token.
# Source: ai-company-oss/bot-role-mapping.md
_ROLE_TOKEN_MAP: dict[str, str] = {
    "triage": "BOT_ORCHESTRATOR",
    "product": "BOT_DESIGN",
    "architect": "BOT_ARCHITECT",
    "frontend": "BOT_FRONTEND",
    "backend": "BOT_BACKEND",
    "qa": "BOT_TESTER",
    "security": "BOT_SECURITY",
    "iac_review": "BOT_DEVOPS",
    "devops": "BOT_DEVOPS",
    "sre": "BOT_DEVOPS",
    "reviewer": "BOT_ORCHESTRATOR",
    "researcher": "BOT_RESEARCHER",
}


class TelegramExecutorError(RuntimeError):
    """Raised when the Telegram executor encounters an unexpected error."""


@dataclass
class TelegramAgentExecutor:
    """Implements :class:`~arvino.ports.AgentExecutor` via Telegram bots and
    Claude Code.

    Each workflow role posts through its own Telegram bot in the shared Arvino
    group.  Agent steps run ``claude -p`` in an isolated git worktree; approval
    steps poll the group for ``/approve`` or ``/reject`` from the owner.

    Parameters
    ----------
    group_id:
        Telegram group chat ID where all bots post.
    owner_telegram_id:
        Telegram user ID of the owner who can approve/reject.  When ``None``,
        any user's ``/approve`` or ``/reject`` is accepted.
    repo_base_dir:
        Local path to the bare or regular clone that worktrees are created from.
    claude_bin:
        Path to the ``claude`` binary.  Defaults to ``"claude"`` (on ``$PATH``).
    model:
        Model name forwarded to ``claude --model``.
    max_turns:
        Maximum agentic turns forwarded to ``claude --max-turns``.
    timeout_seconds:
        ``subprocess.run`` timeout for Claude Code.  ``0`` means no timeout.
    approval_poll_seconds:
        Maximum wall-clock seconds to poll for an owner approval decision.
    poll_interval:
        Seconds between Telegram update polls during approval.
    enable_approval_polling:
        When ``True``, the executor polls Telegram for ``/approve``/``/reject``
        on approval steps.  When ``False``, approval steps return ``running``
        immediately (a separate bot server handles them).
    """

    group_id: str
    owner_telegram_id: str | None = None
    repo_base_dir: Path | None = None
    claude_bin: str = "claude"
    model: str = _DEFAULT_MODEL
    max_turns: int = _DEFAULT_MAX_TURNS
    timeout_seconds: int = 600
    approval_poll_seconds: int = _DEFAULT_APPROVAL_POLL_SECONDS
    poll_interval: float = _DEFAULT_POLL_INTERVAL
    enable_approval_polling: bool = False

    # -- AgentExecutor protocol ------------------------------------------------

    def execute(self, request: StepRequest) -> StepOutcome:
        """Run a single pipeline step via Telegram + Claude Code.

        Posts a task-dispatched message, executes the step (Claude Code for
        agent steps, approval polling for approval steps), posts the result,
        and returns a :class:`StepOutcome`.
        """
        token = self._resolve_token(request.role)
        if token is None:
            return StepOutcome(
                status="failed",
                error=f"No bot token configured for role '{request.role}' "
                f"(set {_ROLE_TOKEN_MAP.get(request.role, 'BOT_<ROLE>')})",
                retryable=False,
            )

        # Announce the task.
        self._send_message(token, self._format_task_message(request))

        # Execute the agent step via Claude Code.
        outcome = self._execute_agent(request)

        # Post result.
        self._send_message(token, self._format_result_message(request, outcome))

        return outcome

    # -- Role / token resolution -----------------------------------------------

    @staticmethod
    def _resolve_token(role: str) -> str | None:
        """Look up the bot token from the environment using the role mapping."""
        env_key = _ROLE_TOKEN_MAP.get(role)
        if env_key is None:
            logger.warning("no bot mapping for role %r", role)
            return None
        token = os.environ.get(env_key)
        if not token:
            logger.warning("bot token env var %s is not set for role %r", env_key, role)
            return None
        return token

    # -- Telegram messaging ----------------------------------------------------

    def _send_message(self, token: str, text: str) -> int | None:
        """Post a message to the Telegram group and return its message_id.

        Returns ``None`` on failure (errors are logged, not raised — the
        executor should keep working even when Telegram is unavailable).
        """
        url = f"{_TELEGRAM_API}/bot{token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": self.group_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    logger.error("telegram sendMessage error: %s", data.get("description"))
                    return None
                result = data.get("result", {})
                return int(result.get("message_id", 0)) or None
        except httpx.HTTPError as exc:
            logger.error("telegram sendMessage failed: %s", exc)
            return None

    def _get_updates(self, token: str, offset: int | None = None) -> list[dict[str, Any]]:
        """Fetch recent Telegram updates (polling)."""
        url = f"{_TELEGRAM_API}/bot{token}/getUpdates"
        params: dict[str, Any] = {"timeout": 10, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        try:
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                if not data.get("ok"):
                    logger.error("telegram getUpdates error: %s", data.get("description"))
                    return []
                return data.get("result", [])
        except httpx.HTTPError as exc:
            logger.error("telegram getUpdates failed: %s", exc)
            return []

    # -- Message formatting ----------------------------------------------------

    @staticmethod
    def _format_task_message(request: StepRequest) -> str:
        """Format the task-dispatched announcement for Telegram."""
        role_display = request.role.replace("_", " ").title()
        lines = [
            "🚀 *Task Dispatched*",
            "",
            f"*Role:* {role_display}",
            f"*Run:* `{request.run_id}`",
            f"*Step:* `{request.step_key}` (attempt #{request.attempt})",
            f"*Issue:* [{request.issue.title}]({request.issue.external_id})",
        ]
        if request.repo:
            lines.append(f"*Repo:* `{request.repo}`")
        if request.upstream_artifacts:
            lines.append("")
            lines.append("*Upstream artifacts:*")
            for art in request.upstream_artifacts:
                lines.append(f"  • {art.name} ({art.kind})")
        lines.append("")
        lines.append("Working… ⏳")
        return "\n".join(lines)

    @staticmethod
    def _format_approval_message(request: StepRequest) -> str:
        """Format an approval request message."""
        role_display = request.role.replace("_", " ").title()
        lines = [
            "🛑 *Approval Required*",
            "",
            f"*Step:* `{request.step_key}`",
            f"*Run:* `{request.run_id}`",
            f"*Role:* {role_display}",
        ]
        if request.issue.title:
            lines.append(f"*Issue:* {request.issue.title}")
        if request.upstream_artifacts:
            lines.append("")
            for art in request.upstream_artifacts:
                if art.kind == "note" and art.metadata:
                    detail = art.metadata.get("summary") or json.dumps(art.metadata, default=str)
                    max_len = 300
                    if len(detail) > max_len:
                        detail = detail[:max_len] + "…"
                    lines.append(f"{detail}")
        lines.append("")
        lines.append("Reply with `/approve` or `/reject <reason>`")
        return "\n".join(lines)

    @staticmethod
    def _format_result_message(request: StepRequest, outcome: StepOutcome) -> str:
        """Format the result message posted after execution."""
        role_display = request.role.replace("_", " ").title()
        if outcome.status == "succeeded":
            icon = "✅"
            header = "*Task Completed*"
        elif outcome.status == "failed":
            icon = "❌"
            header = "*Task Failed*"
        elif outcome.status == "running":
            icon = "⏳"
            header = "*Task In Progress*"
        else:
            icon = "❓"
            header = "*Task Status Unknown*"

        lines = [
            f"{icon} {header}",
            "",
            f"*Role:* {role_display}",
            f"*Step:* `{request.step_key}`",
            f"*Run:* `{request.run_id}`",
        ]

        if outcome.error:
            lines.append("")
            lines.append(f"*Error:* {outcome.error}")

        if outcome.artifacts:
            lines.append("")
            lines.append("*Artifacts:*")
            for art in outcome.artifacts:
                lines.append(f"  • {art.name} ({art.kind})")
                if art.metadata.get("number"):
                    lines.append(f"    PR #{art.metadata['number']}")

        return "\n".join(lines)

    # -- Agent step execution --------------------------------------------------

    def _execute_agent(self, request: StepRequest) -> StepOutcome:
        """Run a Claude Code session for an agent step."""
        worktree_dir: Path | None = None
        try:
            worktree_dir = self._prepare_worktree(request)
            prompt = self._build_prompt(request)
            raw = self._run_claude(prompt, worktree_dir)
            return self._parse_result(raw, request)
        except subprocess.TimeoutExpired:
            logger.warning(
                "claude-code timed out for %s/%s", request.run_id, request.step_key
            )
            return StepOutcome(
                status="failed",
                error=f"Claude Code timed out after {self.timeout_seconds}s",
                retryable=True,
            )
        except TelegramExecutorError as exc:
            logger.exception(
                "claude-code failed for %s/%s", request.run_id, request.step_key
            )
            return StepOutcome(status="failed", error=str(exc), retryable=True)
        except Exception as exc:
            logger.exception("unexpected error in telegram executor")
            return StepOutcome(status="failed", error=str(exc), retryable=True)
        finally:
            if worktree_dir is not None:

                self._cleanup_worktree(worktree_dir)

    def _prepare_worktree(self, request: StepRequest) -> Path:
        """Create an isolated git worktree for the step."""
        base = self._resolve_base_dir(request)
        branch = f"{request.branch_prefix}{request.step_key}"
        worktree_dir = Path(tempfile.mkdtemp(prefix=f"arvino-telegram-{request.step_key}-"))

        try:
            subprocess.run(  # noqa: S603
                ["git", "branch", branch],  # noqa: S607
                cwd=str(base),
                capture_output=True,
                check=False,
            )
            subprocess.run(  # noqa: S603
                ["git", "worktree", "add", str(worktree_dir), branch],  # noqa: S607
                cwd=str(base),
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            import shutil

            shutil.rmtree(worktree_dir, ignore_errors=True)
            raise TelegramExecutorError(
                f"Failed to create worktree: {exc.stderr.decode(errors='replace')}"
            ) from exc

        return worktree_dir

    def _resolve_base_dir(self, request: StepRequest) -> Path:
        if self.repo_base_dir is not None:
            return self.repo_base_dir
        if request.repo is None:
            raise TelegramExecutorError(
                "StepRequest.repo is None and no repo_base_dir configured"
            )
        raise TelegramExecutorError(
            "Auto-cloning from request.repo is not yet implemented; set repo_base_dir"
        )

    def _build_prompt(self, request: StepRequest) -> str:
        """Assemble the Claude Code prompt from the step request."""
        parts: list[str] = []
        parts.append(f"You are acting as the **{request.role}** agent.")
        parts.append(
            f"Run: {request.run_id}  Step: {request.step_key}  Attempt: {request.attempt}"
        )
        parts.append(f"Idempotency key: {request.idempotency_key}")
        parts.append(f"Branch prefix: {request.branch_prefix}")
        parts.append("")

        parts.append(f"## Issue: {request.issue.title}")
        if request.issue.body:
            parts.append("")
            parts.append(request.issue.body)
        parts.append("")

        if request.upstream_artifacts:
            parts.append("## Upstream artifacts")
            for art in request.upstream_artifacts:
                parts.append(f"- **{art.name}** ({art.kind}): {art.uri}")
                if art.metadata:
                    parts.append(f"  metadata: {json.dumps(art.metadata)}")
            parts.append("")

        parts.append("## Rules")
        parts.append(
            f"- Only create branches under the prefix `{request.branch_prefix}`."
        )
        parts.append("- Never merge a pull request.")
        parts.append("- Commit your changes and push when done.")
        if request.repo:
            parts.append(f"- Target repository: {request.repo}")

        return "\n".join(parts)

    def _run_claude(self, prompt: str, cwd: Path) -> dict[str, Any]:
        """Invoke ``claude -p`` and return parsed JSON output."""
        cmd = [
            self.claude_bin,
            "-p",
            prompt,
            "--model",
            self.model,
            "--max-turns",
            str(self.max_turns),
            "--dangerously-skip-permissions",
            "--output-format",
            "json",
            "--allowedTools",
            _DEFAULT_ALLOWED_TOOLS,
        ]

        env = os.environ.copy()
        timeout = self.timeout_seconds if self.timeout_seconds > 0 else None
        result = subprocess.run(  # noqa: S603
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise TelegramExecutorError(
                f"claude exited {result.returncode}: {stderr or result.stdout[:500]}"
            )

        return self._safe_parse_json(result.stdout)

    @staticmethod
    def _safe_parse_json(text: str) -> dict[str, Any]:
        """Parse JSON output, handling JSONL / multi-line quirks."""
        text = text.strip()
        if not text:
            return {}

        for candidate in reversed(text.splitlines()):
            candidate = candidate.strip()
            if candidate.startswith("{"):
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue

        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
        except json.JSONDecodeError:
            return {"raw_output": text}

    def _parse_result(
        self, raw: dict[str, Any], request: StepRequest
    ) -> StepOutcome:
        """Turn Claude Code JSON output into a :class:`StepOutcome`."""
        artifacts: list[ArtifactSpec] = []

        pr_number = self._extract_pr_number(raw)
        if pr_number is not None:
            artifacts.append(
                ArtifactSpec(
                    kind="pull-request",
                    name="pr",
                    uri=f"github://{request.repo}/pulls/{pr_number}"
                    if request.repo
                    else "",
                    metadata={"number": pr_number},
                )
            )

        artifacts.append(
            ArtifactSpec(
                kind="note",
                name=f"{request.step_key}-telegram-output",
                uri=f"claude-code://{request.run_id}/{request.step_key}",
                metadata={"model": self.model, "attempt": request.attempt},
            )
        )

        return StepOutcome(
            status="succeeded",
            output=raw,
            artifacts=artifacts,
            actor=request.actor,
        )

    @staticmethod
    def _extract_pr_number(raw: dict[str, Any]) -> int | None:
        """Best-effort extraction of a PR number from Claude's output."""
        result_text = str(raw.get("result", ""))
        if not result_text:
            return None
        for pattern in (
            r"#(\d+)",
            r"pull/(\d+)",
            r"pull request (?:#)?(\d+)",
        ):
            match = re.search(pattern, result_text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _cleanup_worktree(worktree_dir: Path) -> None:
        """Remove the worktree directory."""
        import shutil

        shutil.rmtree(worktree_dir, ignore_errors=True)

    # -- Approval step execution -----------------------------------------------

    def request_approval(
        self, request: StepRequest, token: str
    ) -> StepOutcome:
        """Handle an approval step by posting a request and (optionally) polling.

        When ``enable_approval_polling`` is ``False``, returns ``running``
        immediately — a separate bot server is expected to handle the
        ``/approve`` / ``/reject`` commands and update the approval via the API.
        When ``True``, polls Telegram until the owner decides or time runs out.
        """
        self._send_message(token, self._format_approval_message(request))

        if not self.enable_approval_polling:
            return StepOutcome(
                status="running",
                output={
                    "approval": "pending",
                    "message": (
                        "Approval request posted to Telegram.  "
                        "A bot server must relay /approve or /reject to the API."
                    ),
                },
                actor=request.actor,
            )

        return self._poll_approval(request, token)

    def _poll_approval(
        self, request: StepRequest, token: str
    ) -> StepOutcome:
        """Poll Telegram getUpdates for /approve or /reject from the owner.

        Each call to getUpdates passes ``offset = last_processed_id + 1`` so
        the Telegram server marks earlier updates as acknowledged and we only
        see new ones.
        """
        deadline = time.monotonic() + self.approval_poll_seconds
        last_processed_id: int = -1  # start before any real update_id

        while time.monotonic() < deadline:
            # Telegram offset semantics: offset = N means "return updates with
            # update_id >= N".  By passing last_processed_id + 1 we acknowledge
            # all updates up to last_processed_id.
            updates = self._get_updates(token, offset=last_processed_id + 1)
            for upd in updates:
                update_id: int = upd.get("update_id", 0)
                if update_id <= last_processed_id:
                    continue
                last_processed_id = max(last_processed_id, update_id)

                message = upd.get("message", {})
                chat = message.get("chat", {})
                from_user = message.get("from", {})
                text = (message.get("text") or "").strip()

                # Only accept messages from the configured group.
                if str(chat.get("id")) != str(self.group_id):
                    continue

                # Only accept from the owner (when configured).
                if (
                    self.owner_telegram_id
                    and str(from_user.get("id")) != str(self.owner_telegram_id)
                ):
                    continue

                if text.startswith("/approve"):
                    return StepOutcome(
                        status="succeeded",
                        output={"approval": "approved", "by": str(from_user.get("id"))},
                        actor=request.actor,
                    )

                if text.startswith("/reject"):
                    reason = text.removeprefix("/reject").strip() or "rejected by owner"
                    return StepOutcome(
                        status="failed",
                        output={"approval": "rejected", "by": str(from_user.get("id"))},
                        error=reason,
                        retryable=False,
                    )

            time.sleep(self.poll_interval)

        return StepOutcome(
            status="failed",
            error=f"Approval timed out after {self.approval_poll_seconds}s",
            output={"approval": "timeout"},
            retryable=True,
        )

    # -- Broadcast helpers (for multi-role announcements) ----------------------

    def broadcast(self, role: str, text: str) -> int | None:
        """Post a message using a specific role's bot.

        Useful for orchestration messages that aren't tied to a single step.
        """
        token = self._resolve_token(role)
        if token is None:
            logger.warning("cannot broadcast: no token for role %r", role)
            return None
        return self._send_message(token, text)

    @staticmethod
    def resolve_bot_username(role: str) -> str | None:
        """Return the bot username for a workflow role (for display / routing).

        Returns ``None`` when the role has no mapping.
        """
        env_key = _ROLE_TOKEN_MAP.get(role)
        if env_key is None:
            return None
        # Usernames are derivable from the env key: BOT_ARCHITECT → arvino_architect_bot
        suffix = env_key.removeprefix("BOT_").lower()
        return f"arvino_{suffix}_bot"