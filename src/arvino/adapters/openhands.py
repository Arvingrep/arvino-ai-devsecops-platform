"""OpenHands Agent Canvas adapter for the AgentExecutor port.

Drives the OpenHands Conversation API (v1.54.0) at
https://agent-canvas.k8shome.com.  Each step gets its own conversation
(isolated sandbox); the adapter posts a goal, polls until the agent
reaches a terminal state, then reads the final response.

Auth is via ``OPENHANDS_API_KEY`` in the environment (mapped to the
``X-Session-API-Key`` request header — never passed on the command line
or emitted in logs).

The adapter is synchronous so it satisfies the :class:`AgentExecutor`
protocol without requiring callers to run an event loop.  Internally it
uses a single :class:`httpx.Client` per call so connections are reused
across the poll loop.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from arvino.domain import ArtifactSpec
from arvino.ports import StepOutcome, StepRequest

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://agent-canvas.k8shome.com"
_DEFAULT_WORKSPACE = "/home/node/workspace"
_DEFAULT_POLL_INTERVAL = 5.0  # seconds between status polls
_DEFAULT_TIMEOUT = 1800  # 30 minutes hard cap
_TERMINAL_STATUSES = frozenset({"completed", "error", "paused", "stopped"})


class OpenHandsError(RuntimeError):
    """Raised when the OpenHands API returns an unexpected error."""


@dataclass
class OpenHandsExecutor:
    """Implements :class:`~arvino.ports.AgentExecutor` via the OpenHands
    Conversation API.

    Parameters
    ----------
    base_url:
        Base URL of the OpenHands Agent Canvas deployment.
    workspace_path:
        Default container-side workspace path handed to new conversations.
    poll_interval:
        Seconds between conversation-status polls.
    timeout_seconds:
        Hard wall-clock limit for the entire execute() call.  ``0`` means
        no limit (not recommended in production).
    confirmation_policy:
        OpenHands confirmation policy forwarded when starting the goal.
        ``"never"`` auto-approves all agent actions.
    http_timeout:
        Per-request ``httpx`` timeout in seconds.
    extra_goal_kwargs:
        Additional fields merged into the goal-start request body.
    """

    base_url: str = _DEFAULT_BASE_URL
    workspace_path: str = _DEFAULT_WORKSPACE
    poll_interval: float = _DEFAULT_POLL_INTERVAL
    timeout_seconds: int = _DEFAULT_TIMEOUT
    confirmation_policy: str = "never"
    http_timeout: float = 30.0
    extra_goal_kwargs: dict[str, Any] = field(default_factory=dict)

    # -- AgentExecutor protocol -----------------------------------------------

    def execute(self, request: StepRequest) -> StepOutcome:
        """Run a single pipeline step via OpenHands and return its outcome."""
        api_key = os.environ.get("OPENHANDS_API_KEY")
        if not api_key:
            return StepOutcome(
                status="failed",
                error="OPENHANDS_API_KEY is not set",
                retryable=False,
            )

        headers = {
            "X-Session-API-Key": api_key,
            "Content-Type": "application/json",
        }
        conv_id: str | None = None

        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=self.http_timeout,
            ) as client:
                conv_id = self._create_conversation(client, request)
                self._start_goal(client, conv_id, request)
                final_info = self._poll_until_done(client, conv_id)
                final_response = self._get_final_response(client, conv_id)
                git_diff = self._get_git_diff(client, conv_id)

            return self._build_outcome(request, conv_id, final_info, final_response, git_diff)

        except OpenHandsError as exc:
            logger.exception(
                "openhands executor failed for %s/%s", request.run_id, request.step_key
            )
            return StepOutcome(status="failed", error=str(exc), retryable=True)
        except httpx.TimeoutException:
            logger.warning(
                "openhands executor timed out for %s/%s (conv=%s)",
                request.run_id,
                request.step_key,
                conv_id,
            )
            return StepOutcome(
                status="failed",
                error=f"OpenHands request timed out after {self.timeout_seconds}s",
                retryable=True,
            )
        except Exception as exc:
            logger.exception("unexpected error in openhands executor")
            return StepOutcome(status="failed", error=str(exc), retryable=True)

    # -- Internals ------------------------------------------------------------

    def _create_conversation(self, client: httpx.Client, request: StepRequest) -> str:
        resp = client.post(
            "/api/conversations",
            json={"workspace": {"path": self.workspace_path}},
        )
        self._raise_for_status(resp, "create conversation")
        data = resp.json()
        conv_id: str = data["id"]
        logger.debug(
            "openhands: created conversation %s for %s/%s",
            conv_id,
            request.run_id,
            request.step_key,
        )
        return conv_id

    def _start_goal(self, client: httpx.Client, conv_id: str, request: StepRequest) -> None:
        goal = self._build_goal(request)
        body: dict[str, Any] = {
            "goal": goal,
            "confirmation_policy": self.confirmation_policy,
            **self.extra_goal_kwargs,
        }
        resp = client.post(f"/api/conversations/{conv_id}/goal", json=body)
        self._raise_for_status(resp, "start goal")

    def _poll_until_done(self, client: httpx.Client, conv_id: str) -> dict[str, Any]:
        """Poll the conversation status until a terminal state is reached."""
        deadline = time.monotonic() + self.timeout_seconds if self.timeout_seconds > 0 else None

        while True:
            resp = client.get(f"/api/conversations/{conv_id}")
            self._raise_for_status(resp, "poll conversation")
            info: dict[str, Any] = resp.json()
            status: str = info.get("status", "")

            if status in _TERMINAL_STATUSES:
                logger.debug("openhands: conversation %s reached status %s", conv_id, status)
                return info

            if deadline is not None and time.monotonic() >= deadline:
                raise OpenHandsError(
                    f"Conversation {conv_id} did not reach a terminal state within "
                    f"{self.timeout_seconds}s (last status: {status!r})"
                )

            time.sleep(self.poll_interval)

    def _get_final_response(self, client: httpx.Client, conv_id: str) -> dict[str, Any]:
        resp = client.get(f"/api/conversations/{conv_id}/agent_final_response")
        if resp.status_code == 404:
            # Agent may not have produced a structured final response.
            return {}
        self._raise_for_status(resp, "get final response")
        result = resp.json()
        return result if isinstance(result, dict) else {"raw": result}

    def _get_git_diff(self, client: httpx.Client, conv_id: str) -> dict[str, Any]:
        resp = client.get(f"/api/conversations/{conv_id}/git/diff")
        if resp.status_code in (404, 422):
            return {}
        self._raise_for_status(resp, "get git diff")
        result = resp.json()
        return result if isinstance(result, dict) else {"raw": result}

    def _build_goal(self, request: StepRequest) -> str:
        """Compose the natural-language goal from the step request."""
        import json as _json

        parts: list[str] = []
        parts.append(f"You are acting as the **{request.role}** agent.")
        parts.append(f"Run: {request.run_id}  Step: {request.step_key}  Attempt: {request.attempt}")
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
                    parts.append(f"  metadata: {_json.dumps(art.metadata)}")
            parts.append("")

        parts.append("## Rules")
        parts.append(f"- Only create branches under the prefix `{request.branch_prefix}`.")
        parts.append("- Never merge a pull request.")
        parts.append("- Commit your changes and push when done.")
        if request.repo:
            parts.append(f"- Target repository: {request.repo}")

        return "\n".join(parts)

    def _build_outcome(
        self,
        request: StepRequest,
        conv_id: str,
        final_info: dict[str, Any],
        final_response: dict[str, Any],
        git_diff: dict[str, Any],
    ) -> StepOutcome:
        oh_status = final_info.get("status", "")
        if oh_status == "error":
            return StepOutcome(
                status="failed",
                error=final_info.get("error")
                or f"OpenHands conversation {conv_id} ended with status 'error'",
                retryable=True,
                output={
                    "conversation_id": conv_id,
                    "openhands_status": oh_status,
                    "final_response": final_response,
                },
            )

        artifacts: list[ArtifactSpec] = []

        # Detect PR from final response text.
        pr_number = self._extract_pr_number(final_response)
        if pr_number is not None:
            artifacts.append(
                ArtifactSpec(
                    kind="pull-request",
                    name="pr",
                    uri=f"github://{request.repo}/pulls/{pr_number}" if request.repo else "",
                    metadata={"number": pr_number},
                )
            )

        # Always emit a note artifact.
        artifacts.append(
            ArtifactSpec(
                kind="note",
                name=f"{request.step_key}-openhands-output",
                uri=f"openhands://{self.base_url}/{conv_id}",
                metadata={
                    "conversation_id": conv_id,
                    "openhands_status": oh_status,
                    "attempt": request.attempt,
                },
            )
        )

        return StepOutcome(
            status="succeeded",
            output={
                "conversation_id": conv_id,
                "openhands_status": oh_status,
                "final_response": final_response,
                "git_diff": git_diff,
            },
            artifacts=artifacts,
            actor=request.actor,
        )

    @staticmethod
    def _extract_pr_number(response: dict[str, Any]) -> int | None:
        """Best-effort extraction of a PR number from the agent's final response."""
        text = str(
            response.get("content") or response.get("result") or response.get("message") or ""
        )
        if not text:
            return None
        for pattern in (r"#(\d+)", r"pull/(\d+)", r"pull request (?:#)?(\d+)"):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return int(match.group(1))
        return None

    @staticmethod
    def _raise_for_status(resp: httpx.Response, operation: str) -> None:
        """Raise :class:`OpenHandsError` for non-2xx responses."""
        if resp.is_error:
            body = resp.text[:500]
            raise OpenHandsError(
                f"OpenHands API error during {operation!r}: HTTP {resp.status_code} — {body}"
            )
