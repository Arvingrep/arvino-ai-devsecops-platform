"""Tests for the Claude Code executor adapter.

These tests mock ``subprocess.run`` to avoid requiring a real ``claude`` binary;
they verify prompt construction, JSON parsing, worktree lifecycle, error
handling, and the resulting :class:`StepOutcome`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from arvino.adapters.claude_code import ClaudeCodeExecutor
from arvino.domain import ArtifactSpec
from arvino.ports import IssueRef, StepRequest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_request(**overrides: Any) -> StepRequest:
    defaults: dict[str, Any] = {
        "run_id": "run-001",
        "step_key": "develop",
        "role": "developer",
        "attempt": 1,
        "idempotency_key": "run-001:develop:1",
        "branch_prefix": "arvino/run-001/",
        "actor": "agent:developer",
        "project_key": "acme",
        "repo": "acme/app",
        "issue": IssueRef(
            id="issue-1",
            source="github",
            external_id="42",
            title="Add user login",
            body="Implement OAuth2 login flow",
            labels=["feature"],
        ),
        "upstream_artifacts": [],
    }
    defaults.update(overrides)
    return StepRequest(**defaults)


@pytest.fixture()
def executor(tmp_path: Path) -> ClaudeCodeExecutor:
    """Executor with repo_base_dir pointed at a real git repo in tmp_path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], capture_output=True, check=True)  # noqa: S603, S607
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],  # noqa: S607
        cwd=str(repo),
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_COMMITTER_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    return ClaudeCodeExecutor(repo_base_dir=repo, timeout_seconds=10)


@pytest.fixture()
def step_request() -> StepRequest:
    return _make_request()


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_contains_role_and_issue(self) -> None:
        ex = ClaudeCodeExecutor()
        req = _make_request()
        prompt = ex._build_prompt(req)
        assert "**developer**" in prompt
        assert "Add user login" in prompt
        assert "OAuth2 login flow" in prompt

    def test_contains_branch_prefix_guardrail(self) -> None:
        ex = ClaudeCodeExecutor()
        req = _make_request(branch_prefix="arvino/run-x/")
        prompt = ex._build_prompt(req)
        assert "arvino/run-x/" in prompt
        assert "Never merge" in prompt

    def test_includes_upstream_artifacts(self) -> None:
        ex = ClaudeCodeExecutor()
        art = ArtifactSpec(
            kind="note",
            name="triage-result",
            uri="git://fake/run-001/triage",
            metadata={"foo": "bar"},
        )
        req = _make_request(upstream_artifacts=[art])
        prompt = ex._build_prompt(req)
        assert "triage-result" in prompt
        assert "git://fake/run-001/triage" in prompt
        assert '"foo"' in prompt

    def test_omits_empty_body(self) -> None:
        ex = ClaudeCodeExecutor()
        req = _make_request(
            issue=IssueRef(id="i", source="gh", external_id="1", title="T", body=""),
        )
        prompt = ex._build_prompt(req)
        assert "## Issue: T" in prompt


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


class TestSafeParseJson:
    def test_simple_json(self) -> None:
        assert ClaudeCodeExecutor._safe_parse_json('{"a": 1}') == {"a": 1}

    def test_jsonl_takes_last(self) -> None:
        text = '{"x": 1}\n{"y": 2}'
        assert ClaudeCodeExecutor._safe_parse_json(text) == {"y": 2}

    def test_empty(self) -> None:
        assert ClaudeCodeExecutor._safe_parse_json("") == {}
        assert ClaudeCodeExecutor._safe_parse_json("   ") == {}

    def test_non_dict_wrapped(self) -> None:
        result = ClaudeCodeExecutor._safe_parse_json("[1,2,3]")
        assert result == {"raw": [1, 2, 3]}

    def test_garbage_returns_raw_output(self) -> None:
        result = ClaudeCodeExecutor._safe_parse_json("not json at all")
        assert result == {"raw_output": "not json at all"}


# ---------------------------------------------------------------------------
# PR number extraction
# ---------------------------------------------------------------------------


class TestExtractPrNumber:
    def test_hash_pattern(self) -> None:
        assert ClaudeCodeExecutor._extract_pr_number({"result": "Opened PR #42"}) == 42

    def test_pull_slash_pattern(self) -> None:
        assert ClaudeCodeExecutor._extract_pr_number({"result": "See pull/99"}) == 99

    def test_no_pr(self) -> None:
        assert ClaudeCodeExecutor._extract_pr_number({"result": "Done!"}) is None
        assert ClaudeCodeExecutor._extract_pr_number({}) is None


# ---------------------------------------------------------------------------
# Token guard
# ---------------------------------------------------------------------------


class TestTokenGuard:
    def test_missing_token_returns_failed(self) -> None:
        ex = ClaudeCodeExecutor()
        req = _make_request()
        with patch.dict(os.environ, {}, clear=True):
            # Ensure CLAUDE_CODE_OAUTH_TOKEN is not set
            os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
            outcome = ex.execute(req)
        assert outcome.status == "failed"
        assert "CLAUDE_CODE_OAUTH_TOKEN" in (outcome.error or "")
        assert outcome.retryable is False


# ---------------------------------------------------------------------------
# Full execute (mocked subprocess)
# ---------------------------------------------------------------------------


_OK = subprocess.CompletedProcess([], 0, stdout="", stderr="")


class TestExecuteHappyPath:
    def test_succeeds_with_note_artifact(
        self, executor: ClaudeCodeExecutor, step_request: StepRequest
    ) -> None:
        claude_output = json.dumps({"result": "All changes committed.", "cost_usd": 0.03})

        with (
            patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}),
            patch("arvino.adapters.claude_code.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,  # git branch
                _OK,  # git worktree add
                subprocess.CompletedProcess([], 0, stdout=claude_output, stderr=""),
                _OK,  # git worktree remove
            ]
            outcome = executor.execute(step_request)

        assert outcome.status == "succeeded"
        assert outcome.actor == "agent:developer"
        assert any(a.kind == "note" for a in outcome.artifacts)
        note = next(a for a in outcome.artifacts if a.kind == "note")
        assert "develop" in note.name

    def test_pr_artifact_when_pr_created(
        self, executor: ClaudeCodeExecutor, step_request: StepRequest
    ) -> None:
        claude_output = json.dumps({"result": "Created pull request #7 for the feature."})

        with (
            patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}),
            patch("arvino.adapters.claude_code.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,
                _OK,
                subprocess.CompletedProcess([], 0, stdout=claude_output, stderr=""),
                _OK,
            ]
            outcome = executor.execute(step_request)

        assert outcome.status == "succeeded"
        pr_arts = [a for a in outcome.artifacts if a.kind == "pull-request"]
        assert len(pr_arts) == 1
        assert pr_arts[0].metadata["number"] == 7
        assert "acme/app" in pr_arts[0].uri


class TestExecuteErrors:
    def test_claude_nonzero_exit_returns_failed(
        self, executor: ClaudeCodeExecutor, step_request: StepRequest
    ) -> None:
        with (
            patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}),
            patch("arvino.adapters.claude_code.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,
                _OK,
                subprocess.CompletedProcess([], 1, stdout="", stderr="rate limited"),
                _OK,
            ]
            outcome = executor.execute(step_request)

        assert outcome.status == "failed"
        assert "rate limited" in (outcome.error or "")
        assert outcome.retryable is True

    def test_timeout_returns_retryable(
        self, executor: ClaudeCodeExecutor, step_request: StepRequest
    ) -> None:
        with (
            patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}),
            patch("arvino.adapters.claude_code.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,
                _OK,
                subprocess.TimeoutExpired(cmd="claude", timeout=10),
                _OK,
            ]
            outcome = executor.execute(step_request)

        assert outcome.status == "failed"
        assert "timed out" in (outcome.error or "")
        assert outcome.retryable is True

    def test_worktree_failure_returns_failed(
        self, executor: ClaudeCodeExecutor, step_request: StepRequest
    ) -> None:
        with (
            patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}),
            patch("arvino.adapters.claude_code.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,
                subprocess.CalledProcessError(128, "git", stderr=b"fatal: bad ref"),
            ]
            outcome = executor.execute(step_request)

        assert outcome.status == "failed"
        assert outcome.retryable is True

    def test_no_repo_base_dir_and_no_repo_fails(self) -> None:
        ex = ClaudeCodeExecutor()  # no repo_base_dir
        req = _make_request(repo=None)
        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}):
            outcome = ex.execute(req)
        assert outcome.status == "failed"
        assert "repo" in (outcome.error or "").lower()


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


class TestCommandConstruction:
    def test_passes_expected_flags(
        self, executor: ClaudeCodeExecutor, step_request: StepRequest
    ) -> None:
        captured_cmd: list[str] = []

        def capture_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if args and isinstance(args[0], list) and args[0][0] == executor.claude_bin:
                captured_cmd.extend(args[0])
                return subprocess.CompletedProcess(args[0], 0, stdout="{}", stderr="")
            return subprocess.CompletedProcess(args[0] if args else [], 0, stdout="", stderr="")

        with (
            patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}),
            patch("arvino.adapters.claude_code.subprocess.run", side_effect=capture_run),
        ):
            executor.execute(step_request)

        assert "--dangerously-skip-permissions" in captured_cmd
        assert "--output-format" in captured_cmd
        assert "json" in captured_cmd
        assert "--model" in captured_cmd
        assert "sonnet" in captured_cmd
        assert "--allowedTools" in captured_cmd

    def test_env_includes_token(
        self, executor: ClaudeCodeExecutor, step_request: StepRequest
    ) -> None:
        captured_env: dict[str, str] = {}

        def capture_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if (
                "env" in kwargs
                and args
                and isinstance(args[0], list)
                and len(args[0]) > 0
                and args[0][0] == executor.claude_bin
            ):
                captured_env.update(kwargs["env"])
            return subprocess.CompletedProcess(args[0] if args else [], 0, stdout="{}", stderr="")

        with (
            patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-secret"}),
            patch("arvino.adapters.claude_code.subprocess.run", side_effect=capture_run),
        ):
            executor.execute(step_request)

        assert captured_env.get("CLAUDE_CODE_OAUTH_TOKEN") == "tok-secret"


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_satisfies_agent_executor_protocol(self) -> None:
        """ClaudeCodeExecutor must structurally match AgentExecutor."""
        from arvino.ports import AgentExecutor

        ex: AgentExecutor = ClaudeCodeExecutor()  # type: ignore[assignment]
        assert hasattr(ex, "execute")
        assert callable(ex.execute)
