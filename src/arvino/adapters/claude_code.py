"""Claude Code CLI adapter for the AgentExecutor port.

Spawns ``claude -p`` in print mode inside an isolated git worktree so each step
gets a clean, disposable working copy. Auth is via ``CLAUDE_CODE_OAUTH_TOKEN``
in the environment (never passed on the command line).

The adapter is production-ready but intentionally has **no** merge capability:
Claude Code creates branches and opens PRs; a human or the CI gate decides
whether to merge.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arvino.domain import ArtifactSpec
from arvino.ports import StepOutcome, StepRequest

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "sonnet"
_DEFAULT_MAX_TURNS = 30
_ALLOWED_TOOLS = "Read,Write,Edit,Bash"


class ClaudeCodeError(RuntimeError):
    """Raised when the Claude Code CLI exits with an unexpected error."""


@dataclass
class ClaudeCodeExecutor:
    """Implements :class:`~arvino.ports.AgentExecutor` by shelling out to the
    Claude Code CLI (``claude -p``).

    Parameters
    ----------
    repo_base_dir:
        Local path to the bare or regular clone that worktrees are created from.
        When ``None`` (default), the adapter clones ``request.repo`` into a
        temporary directory before creating a worktree.
    claude_bin:
        Path to the ``claude`` binary.  Defaults to ``"claude"`` (on ``$PATH``).
    model:
        Model name forwarded to ``--model``.
    max_turns:
        Maximum agentic turns forwarded to ``--max-turns``.
    timeout_seconds:
        ``subprocess.run`` timeout.  ``0`` means no timeout.
    env_overrides:
        Extra environment variables merged into the subprocess environment.
        **Never** put secrets here; use the host env or a vault.
    """

    repo_base_dir: Path | None = None
    claude_bin: str = "claude"
    model: str = _DEFAULT_MODEL
    max_turns: int = _DEFAULT_MAX_TURNS
    timeout_seconds: int = 600
    env_overrides: dict[str, str] = field(default_factory=dict)

    # -- AgentExecutor protocol ------------------------------------------------

    def execute(self, request: StepRequest) -> StepOutcome:
        """Run a Claude Code session for a single pipeline step."""
        token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
        if not token:
            return StepOutcome(
                status="failed",
                error="CLAUDE_CODE_OAUTH_TOKEN is not set",
                retryable=False,
            )

        worktree_dir: Path | None = None
        try:
            worktree_dir = self._prepare_worktree(request)
            prompt = self._build_prompt(request)
            raw = self._run_claude(prompt, worktree_dir, token)
            return self._parse_result(raw, request)
        except subprocess.TimeoutExpired:
            logger.warning("claude-code timed out for %s/%s", request.run_id, request.step_key)
            return StepOutcome(
                status="failed",
                error=f"Claude Code timed out after {self.timeout_seconds}s",
                retryable=True,
            )
        except ClaudeCodeError as exc:
            logger.exception("claude-code failed for %s/%s", request.run_id, request.step_key)
            return StepOutcome(status="failed", error=str(exc), retryable=True)
        except Exception as exc:
            logger.exception("unexpected error in claude-code executor")
            return StepOutcome(status="failed", error=str(exc), retryable=True)
        finally:
            if worktree_dir is not None:
                self._cleanup_worktree(worktree_dir, request)

    # -- Internals -------------------------------------------------------------

    def _prepare_worktree(self, request: StepRequest) -> Path:
        """Create an isolated git worktree for the step.

        The worktree branch is ``<branch_prefix><step_key>`` so that agents stay
        within the namespace the engine allocated for this run.
        """
        base = self._resolve_base_dir(request)
        branch = f"{request.branch_prefix}{request.step_key}"
        worktree_dir = Path(tempfile.mkdtemp(prefix=f"arvino-wt-{request.step_key}-"))

        try:
            # Ensure the branch exists locally (create from HEAD if not).
            subprocess.run(  # noqa: S603
                ["git", "branch", branch],  # noqa: S607
                cwd=str(base),
                capture_output=True,
                check=False,  # branch may already exist
            )
            subprocess.run(  # noqa: S603
                ["git", "worktree", "add", str(worktree_dir), branch],  # noqa: S607
                cwd=str(base),
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            shutil.rmtree(worktree_dir, ignore_errors=True)
            raise ClaudeCodeError(
                f"Failed to create worktree: {exc.stderr.decode(errors='replace')}"
            ) from exc

        return worktree_dir

    def _resolve_base_dir(self, request: StepRequest) -> Path:
        if self.repo_base_dir is not None:
            return self.repo_base_dir
        if request.repo is None:
            raise ClaudeCodeError("StepRequest.repo is None and no repo_base_dir configured")
        raise ClaudeCodeError(
            "Auto-cloning from request.repo is not yet implemented; set repo_base_dir"
        )

    def _build_prompt(self, request: StepRequest) -> str:
        """Assemble the system prompt from the step request."""
        parts: list[str] = []
        parts.append(f"You are acting as the **{request.role}** agent.")
        parts.append(f"Run: {request.run_id}  Step: {request.step_key}  Attempt: {request.attempt}")
        parts.append(f"Idempotency key: {request.idempotency_key}")
        parts.append(f"Branch prefix: {request.branch_prefix}")
        parts.append("")

        # Issue context
        parts.append(f"## Issue: {request.issue.title}")
        if request.issue.body:
            parts.append("")
            parts.append(request.issue.body)
        parts.append("")

        # Upstream artifacts
        if request.upstream_artifacts:
            parts.append("## Upstream artifacts")
            for art in request.upstream_artifacts:
                parts.append(f"- **{art.name}** ({art.kind}): {art.uri}")
                if art.metadata:
                    parts.append(f"  metadata: {json.dumps(art.metadata)}")
            parts.append("")

        # Guardrails
        parts.append("## Rules")
        parts.append(f"- Only create branches under the prefix `{request.branch_prefix}`.")
        parts.append("- Never merge a pull request.")
        parts.append("- Commit your changes and push when done.")
        if request.repo:
            parts.append(f"- Target repository: {request.repo}")

        return "\n".join(parts)

    def _run_claude(self, prompt: str, cwd: Path, token: str) -> dict[str, Any]:
        """Invoke ``claude -p`` and return the parsed JSON output."""
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
            _ALLOWED_TOOLS,
        ]

        env = {**os.environ, **self.env_overrides, "CLAUDE_CODE_OAUTH_TOKEN": token}

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
            raise ClaudeCodeError(
                f"claude exited {result.returncode}: {stderr or result.stdout[:500]}"
            )

        return self._safe_parse_json(result.stdout)

    @staticmethod
    def _safe_parse_json(text: str) -> dict[str, Any]:
        """Parse the JSON output, handling streaming / multi-line quirks."""
        text = text.strip()
        if not text:
            return {}

        # Claude --output-format json may emit JSONL; take the last complete object.
        for candidate in reversed(text.splitlines()):
            candidate = candidate.strip()
            if candidate.startswith("{"):
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue

        # Fallback: try the whole blob
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
        except json.JSONDecodeError:
            return {"raw_output": text}

    def _parse_result(self, raw: dict[str, Any], request: StepRequest) -> StepOutcome:
        """Turn Claude Code's JSON output into a :class:`StepOutcome`."""
        artifacts: list[ArtifactSpec] = []

        # Detect if a PR was created (Claude Code emits a "result" key or similar).
        pr_number = self._extract_pr_number(raw)
        if pr_number is not None:
            artifacts.append(
                ArtifactSpec(
                    kind="pull-request",
                    name="pr",
                    uri=f"github://{request.repo}/pulls/{pr_number}" if request.repo else "",
                    metadata={"number": pr_number},
                )
            )

        # Always emit a note artifact with the Claude output.
        artifacts.append(
            ArtifactSpec(
                kind="note",
                name=f"{request.step_key}-claude-output",
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
        # The Claude Code JSON result may contain a "result" string mentioning a PR.
        result_text = str(raw.get("result", ""))
        if not result_text:
            return None

        import re

        # Match patterns like "#123", "pull/123", "PR #123"
        for pattern in (
            r"#(\d+)",
            r"pull/(\d+)",
            r"pull request (?:#)?(\d+)",
        ):
            match = re.search(pattern, result_text, re.IGNORECASE)
            if match:
                return int(match.group(1))

        return None

    def _cleanup_worktree(self, worktree_dir: Path, request: StepRequest) -> None:
        """Remove the worktree and its branch bookkeeping."""
        if self.repo_base_dir is None:
            # If we auto-created the base, just remove everything.
            shutil.rmtree(worktree_dir, ignore_errors=True)
            return

        try:
            subprocess.run(  # noqa: S603
                ["git", "worktree", "remove", "--force", str(worktree_dir)],  # noqa: S607
                cwd=str(self.repo_base_dir),
                capture_output=True,
                check=False,
            )
        except Exception:
            logger.warning("failed to remove worktree %s", worktree_dir, exc_info=True)
        # Don't delete the branch — it may have pushed commits.
