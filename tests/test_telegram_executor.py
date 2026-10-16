"""Tests for the Telegram Agent Executor adapter.

These tests mock subprocess, httpx, and os.environ to avoid requiring a real
Telegram bot or ``claude`` binary.  They verify role-token resolution, message
formatting, Claude Code prompt construction, JSON parsing, error handling,
approval polling, and protocol compliance.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from arvino.adapters.telegram_executor import TelegramAgentExecutor
from arvino.domain import ArtifactSpec
from arvino.ports import IssueRef, StepOutcome, StepRequest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GROUP_ID = "-5351926143"
OWNER_ID = "123456789"


def _make_request(**overrides: Any) -> StepRequest:
    defaults: dict[str, Any] = {
        "run_id": "run-001",
        "step_key": "develop",
        "role": "backend",
        "attempt": 1,
        "idempotency_key": "run-001:develop:1",
        "branch_prefix": "arvino/run-001/",
        "actor": "agent:backend",
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
def executor(tmp_path: Path) -> TelegramAgentExecutor:
    """Executor with a real git repo in tmp_path and mock bot tokens."""
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
    return TelegramAgentExecutor(
        group_id=GROUP_ID,
        owner_telegram_id=OWNER_ID,
        repo_base_dir=repo,
        timeout_seconds=10,
    )


@pytest.fixture()
def step_request() -> StepRequest:
    return _make_request()


@pytest.fixture()
def bot_env() -> dict[str, str]:
    """Fake bot tokens covering all roles."""
    return {
        "BOT_ORCHESTRATOR": "tok-orch",
        "BOT_ARCHITECT": "tok-arch",
        "BOT_DESIGN": "tok-design",
        "BOT_FRONTEND": "tok-fe",
        "BOT_BACKEND": "tok-be",
        "BOT_SMARTCONTRACT": "tok-sc",
        "BOT_RESEARCHER": "tok-res",
        "BOT_TESTER": "tok-test",
        "BOT_SECURITY": "tok-sec",
        "BOT_DEVOPS": "tok-devops",
    }


# ---------------------------------------------------------------------------
# Role → token resolution
# ---------------------------------------------------------------------------


class TestResolveToken:
    def test_known_role(self, bot_env: dict[str, str]) -> None:
        with patch.dict(os.environ, bot_env):
            assert TelegramAgentExecutor._resolve_token("backend") == "tok-be"

    def test_devops_roles_share_token(self, bot_env: dict[str, str]) -> None:
        with patch.dict(os.environ, bot_env):
            assert TelegramAgentExecutor._resolve_token("devops") == "tok-devops"
            assert TelegramAgentExecutor._resolve_token("sre") == "tok-devops"
            assert TelegramAgentExecutor._resolve_token("iac_review") == "tok-devops"

    def test_orchestrator_roles_share_token(self, bot_env: dict[str, str]) -> None:
        with patch.dict(os.environ, bot_env):
            assert TelegramAgentExecutor._resolve_token("triage") == "tok-orch"
            assert TelegramAgentExecutor._resolve_token("reviewer") == "tok-orch"

    def test_unknown_role_returns_none(self) -> None:
        assert TelegramAgentExecutor._resolve_token("nonexistent") is None

    def test_missing_env_var(self, bot_env: dict[str, str]) -> None:
        env = bot_env.copy()
        del env["BOT_BACKEND"]
        with patch.dict(os.environ, env):
            assert TelegramAgentExecutor._resolve_token("backend") is None


# ---------------------------------------------------------------------------
# resolve_bot_username
# ---------------------------------------------------------------------------


class TestResolveBotUsername:
    def test_architect(self) -> None:
        assert TelegramAgentExecutor.resolve_bot_username("architect") == "arvino_architect_bot"

    def test_unknown_role(self) -> None:
        assert TelegramAgentExecutor.resolve_bot_username("nonexistent") is None


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------


class TestFormatTaskMessage:
    def test_contains_role_and_issue(self) -> None:
        req = _make_request()
        msg = TelegramAgentExecutor._format_task_message(req)
        assert "Backend" in msg
        assert "Add user login" in msg
        assert "run-001" in msg
        assert "develop" in msg

    def test_includes_repo(self) -> None:
        req = _make_request(repo="acme/app")
        msg = TelegramAgentExecutor._format_task_message(req)
        assert "acme/app" in msg

    def test_includes_upstream_artifacts(self) -> None:
        art = ArtifactSpec(kind="note", name="triage-result", uri="git://x", metadata={"foo": "bar"})
        req = _make_request(upstream_artifacts=[art])
        msg = TelegramAgentExecutor._format_task_message(req)
        assert "triage-result" in msg
        assert "note" in msg


class TestFormatApprovalMessage:
    def test_contains_instructions(self) -> None:
        req = _make_request()
        msg = TelegramAgentExecutor._format_approval_message(req)
        assert "/approve" in msg
        assert "/reject" in msg
        assert "develop" in msg

    def test_includes_upstream_note_details(self) -> None:
        art = ArtifactSpec(
            kind="note",
            name="arch-review",
            uri="git://x",
            metadata={"summary": "Approved with caveats"},
        )
        req = _make_request(upstream_artifacts=[art])
        msg = TelegramAgentExecutor._format_approval_message(req)
        assert "Approved with caveats" in msg


class TestFormatResultMessage:
    def test_succeeded(self) -> None:
        req = _make_request()
        outcome = StepOutcome(status="succeeded", artifacts=[], actor="agent:backend")
        msg = TelegramAgentExecutor._format_result_message(req, outcome)
        assert "✅" in msg
        assert "Completed" in msg

    def test_failed(self) -> None:
        req = _make_request()
        outcome = StepOutcome(
            status="failed", error="something broke", artifacts=[], actor="agent:backend"
        )
        msg = TelegramAgentExecutor._format_result_message(req, outcome)
        assert "❌" in msg
        assert "something broke" in msg

    def test_running(self) -> None:
        req = _make_request()
        outcome = StepOutcome(status="running", artifacts=[], actor="agent:backend")
        msg = TelegramAgentExecutor._format_result_message(req, outcome)
        assert "In Progress" in msg

    def test_includes_pr_artifact(self) -> None:
        req = _make_request()
        pr = ArtifactSpec(kind="pull-request", name="pr", uri="", metadata={"number": 42})
        outcome = StepOutcome(status="succeeded", artifacts=[pr], actor="agent:backend")
        msg = TelegramAgentExecutor._format_result_message(req, outcome)
        assert "PR #42" in msg


# ---------------------------------------------------------------------------
# Claude Code prompt building
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_contains_role_and_issue(self) -> None:
        ex = TelegramAgentExecutor(group_id=GROUP_ID)
        req = _make_request()
        prompt = ex._build_prompt(req)
        assert "**backend**" in prompt
        assert "Add user login" in prompt
        assert "OAuth2 login flow" in prompt

    def test_contains_branch_prefix_guardrail(self) -> None:
        ex = TelegramAgentExecutor(group_id=GROUP_ID)
        req = _make_request(branch_prefix="arvino/run-x/")
        prompt = ex._build_prompt(req)
        assert "arvino/run-x/" in prompt
        assert "Never merge" in prompt

    def test_includes_upstream_artifacts(self) -> None:
        ex = TelegramAgentExecutor(group_id=GROUP_ID)
        art = ArtifactSpec(kind="note", name="triage-result", uri="git://fake/run-001/triage",
                           metadata={"foo": "bar"})
        req = _make_request(upstream_artifacts=[art])
        prompt = ex._build_prompt(req)
        assert "triage-result" in prompt
        assert "git://fake/run-001/triage" in prompt
        assert '"foo"' in prompt


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------


class TestSafeParseJson:
    def test_simple_json(self) -> None:
        assert TelegramAgentExecutor._safe_parse_json('{"a": 1}') == {"a": 1}

    def test_jsonl_takes_last(self) -> None:
        assert TelegramAgentExecutor._safe_parse_json('{"x": 1}\n{"y": 2}') == {"y": 2}

    def test_empty(self) -> None:
        assert TelegramAgentExecutor._safe_parse_json("") == {}
        assert TelegramAgentExecutor._safe_parse_json("   ") == {}

    def test_non_dict_wrapped(self) -> None:
        result = TelegramAgentExecutor._safe_parse_json("[1,2,3]")
        assert result == {"raw": [1, 2, 3]}

    def test_garbage_returns_raw_output(self) -> None:
        result = TelegramAgentExecutor._safe_parse_json("not json at all")
        assert result == {"raw_output": "not json at all"}


# ---------------------------------------------------------------------------
# PR number extraction
# ---------------------------------------------------------------------------


class TestExtractPrNumber:
    def test_hash_pattern(self) -> None:
        assert TelegramAgentExecutor._extract_pr_number({"result": "Opened PR #42"}) == 42

    def test_pull_slash_pattern(self) -> None:
        assert TelegramAgentExecutor._extract_pr_number({"result": "See pull/99"}) == 99

    def test_no_pr(self) -> None:
        assert TelegramAgentExecutor._extract_pr_number({"result": "Done!"}) is None
        assert TelegramAgentExecutor._extract_pr_number({}) is None


# ---------------------------------------------------------------------------
# Token guard — missing bot token
# ---------------------------------------------------------------------------


class TestTokenGuard:
    def test_missing_bot_token_returns_failed(self) -> None:
        ex = TelegramAgentExecutor(group_id=GROUP_ID)
        req = _make_request(role="backend")
        with patch.dict(os.environ, {}, clear=True):
            outcome = ex.execute(req)
        assert outcome.status == "failed"
        assert "BOT_BACKEND" in (outcome.error or "")
        assert outcome.retryable is False

    def test_unknown_role_returns_failed(self) -> None:
        ex = TelegramAgentExecutor(group_id=GROUP_ID)
        req = _make_request(role="unknown_role")
        with patch.dict(os.environ, {"BOT_BACKEND": "tok"}):
            outcome = ex.execute(req)
        assert outcome.status == "failed"
        assert "unknown_role" in (outcome.error or "")
        assert outcome.retryable is False


# ---------------------------------------------------------------------------
# Full execute (mocked subprocess + httpx)
# ---------------------------------------------------------------------------

_OK = subprocess.CompletedProcess([], 0, stdout="", stderr="")


class TestExecuteHappyPath:
    def test_succeeds_with_note_artifact_and_posts_messages(
        self, executor: TelegramAgentExecutor, step_request: StepRequest
    ) -> None:
        claude_output = json.dumps({"result": "All changes committed.", "cost_usd": 0.03})
        mock_send = MagicMock(return_value=42)

        with (
            patch.dict(os.environ, {"BOT_BACKEND": "tok-be"}),
            patch.object(executor, "_send_message", mock_send),
            patch("arvino.adapters.telegram_executor.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,  # git branch
                _OK,  # git worktree add
                subprocess.CompletedProcess([], 0, stdout=claude_output, stderr=""),
                _OK,  # worktree cleanup (shutil.rmtree is called instead)
            ]
            outcome = executor.execute(step_request)

        assert outcome.status == "succeeded"
        assert outcome.actor == "agent:backend"
        assert any(a.kind == "note" for a in outcome.artifacts)
        note = next(a for a in outcome.artifacts if a.kind == "note")
        assert "develop" in note.name

        # Should have sent task + result messages.
        assert mock_send.call_count == 2

    def test_pr_artifact_when_pr_created(
        self, executor: TelegramAgentExecutor, step_request: StepRequest
    ) -> None:
        claude_output = json.dumps({"result": "Created pull request #7"})

        with (
            patch.dict(os.environ, {"BOT_BACKEND": "tok-be"}),
            patch.object(executor, "_send_message", MagicMock(return_value=42)),
            patch("arvino.adapters.telegram_executor.subprocess.run") as mock_run,
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

    def test_telegram_failure_does_not_block_execution(
        self, executor: TelegramAgentExecutor, step_request: StepRequest
    ) -> None:
        """When Telegram posting fails, execution should still proceed and succeed."""
        claude_output = json.dumps({"result": "Done"})

        with (
            patch.dict(os.environ, {"BOT_BACKEND": "tok-be"}),
            patch.object(executor, "_send_message", MagicMock(return_value=None)),
            patch("arvino.adapters.telegram_executor.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,
                _OK,
                subprocess.CompletedProcess([], 0, stdout=claude_output, stderr=""),
                _OK,
            ]
            outcome = executor.execute(step_request)

        assert outcome.status == "succeeded"


class TestExecuteErrors:
    def test_claude_nonzero_exit_returns_failed(
        self, executor: TelegramAgentExecutor, step_request: StepRequest
    ) -> None:
        with (
            patch.dict(os.environ, {"BOT_BACKEND": "tok-be"}),
            patch.object(executor, "_send_message", MagicMock(return_value=42)),
            patch("arvino.adapters.telegram_executor.subprocess.run") as mock_run,
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
        self, executor: TelegramAgentExecutor, step_request: StepRequest
    ) -> None:
        with (
            patch.dict(os.environ, {"BOT_BACKEND": "tok-be"}),
            patch.object(executor, "_send_message", MagicMock(return_value=42)),
            patch("arvino.adapters.telegram_executor.subprocess.run") as mock_run,
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
        self, executor: TelegramAgentExecutor, step_request: StepRequest
    ) -> None:
        with (
            patch.dict(os.environ, {"BOT_BACKEND": "tok-be"}),
            patch.object(executor, "_send_message", MagicMock(return_value=42)),
            patch("arvino.adapters.telegram_executor.subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                _OK,
                subprocess.CalledProcessError(128, "git", stderr=b"fatal: bad ref"),
            ]
            outcome = executor.execute(step_request)

        assert outcome.status == "failed"
        assert outcome.retryable is True

    def test_no_repo_base_dir_and_no_repo_fails(
        self, step_request: StepRequest
    ) -> None:
        ex = TelegramAgentExecutor(group_id=GROUP_ID)
        req = _make_request(repo=None)
        with (
            patch.dict(os.environ, {"BOT_BACKEND": "tok-be"}),
            patch.object(ex, "_send_message", MagicMock(return_value=42)),
        ):
            outcome = ex.execute(req)
        assert outcome.status == "failed"
        assert "repo" in (outcome.error or "").lower()


# ---------------------------------------------------------------------------
# Approval polling
# ---------------------------------------------------------------------------


class TestApprovalPolling:
    def test_polling_disabled_returns_running(self, bot_env: dict[str, str]) -> None:
        ex = TelegramAgentExecutor(
            group_id=GROUP_ID, enable_approval_polling=False
        )
        req = _make_request()
        with (
            patch.dict(os.environ, bot_env),
            patch.object(ex, "_send_message", MagicMock(return_value=42)),
        ):
            outcome = ex.request_approval(req, "tok-orch")
        assert outcome.status == "running"
        assert outcome.output["approval"] == "pending"

    def test_poll_approval_approve(self, bot_env: dict[str, str]) -> None:
        ex = TelegramAgentExecutor(
            group_id=GROUP_ID,
            owner_telegram_id=OWNER_ID,
            enable_approval_polling=True,
            approval_poll_seconds=5,
            poll_interval=0.1,
        )
        req = _make_request()

        # Simulate one getUpdates call returning an /approve message.
        approve_update = {
            "update_id": 100,
            "message": {
                "message_id": 1,
                "chat": {"id": int(GROUP_ID)},
                "from": {"id": int(OWNER_ID), "first_name": "Owner"},
                "text": "/approve",
            },
        }

        with (
            patch.dict(os.environ, bot_env),
            patch.object(ex, "_send_message", MagicMock(return_value=42)),
            patch.object(ex, "_get_updates", return_value=[approve_update]),
        ):
            outcome = ex.request_approval(req, "tok-orch")

        assert outcome.status == "succeeded"
        assert outcome.output["approval"] == "approved"

    def test_poll_approval_reject(self, bot_env: dict[str, str]) -> None:
        ex = TelegramAgentExecutor(
            group_id=GROUP_ID,
            owner_telegram_id=OWNER_ID,
            enable_approval_polling=True,
            approval_poll_seconds=5,
            poll_interval=0.1,
        )
        req = _make_request()

        reject_update = {
            "update_id": 101,
            "message": {
                "message_id": 2,
                "chat": {"id": int(GROUP_ID)},
                "from": {"id": int(OWNER_ID), "first_name": "Owner"},
                "text": "/reject not ready yet",
            },
        }

        with (
            patch.dict(os.environ, bot_env),
            patch.object(ex, "_send_message", MagicMock(return_value=42)),
            patch.object(ex, "_get_updates", return_value=[reject_update]),
        ):
            outcome = ex.request_approval(req, "tok-orch")

        assert outcome.status == "failed"
        assert outcome.output["approval"] == "rejected"
        assert "not ready yet" in (outcome.error or "")
        assert outcome.retryable is False

    def test_poll_approval_ignores_other_users(self, bot_env: dict[str, str]) -> None:
        ex = TelegramAgentExecutor(
            group_id=GROUP_ID,
            owner_telegram_id=OWNER_ID,
            enable_approval_polling=True,
            approval_poll_seconds=5,
            poll_interval=0.1,
        )
        req = _make_request()

        non_owner_update = {
            "update_id": 102,
            "message": {
                "message_id": 3,
                "chat": {"id": int(GROUP_ID)},
                "from": {"id": 99999, "first_name": "Random"},
                "text": "/approve",
            },
        }

        # Return non-owner update once, then empty lists — poll should time out.
        calls: list[list[dict[str, Any]]] = [[non_owner_update]]

        def fake_updates(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            if calls:
                return calls.pop(0)
            return []

        with (
            patch.dict(os.environ, bot_env),
            patch.object(ex, "_send_message", MagicMock(return_value=42)),
            patch.object(ex, "_get_updates", side_effect=fake_updates),
        ):
            outcome = ex.request_approval(req, "tok-orch")

        assert outcome.status == "failed"
        assert "timed out" in (outcome.error or "")

    def test_poll_approval_ignores_other_chats(self, bot_env: dict[str, str]) -> None:
        ex = TelegramAgentExecutor(
            group_id=GROUP_ID,
            owner_telegram_id=OWNER_ID,
            enable_approval_polling=True,
            approval_poll_seconds=5,
            poll_interval=0.1,
        )
        req = _make_request()

        other_chat_update = {
            "update_id": 103,
            "message": {
                "message_id": 4,
                "chat": {"id": int(OWNER_ID)},
                "from": {"id": int(OWNER_ID), "first_name": "Owner"},
                "text": "/approve",
            },
        }

        # Return other-chat update once, then empty lists — poll should time out.
        calls: list[list[dict[str, Any]]] = [[other_chat_update]]

        def fake_updates(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            if calls:
                return calls.pop(0)
            return []

        with (
            patch.dict(os.environ, bot_env),
            patch.object(ex, "_send_message", MagicMock(return_value=42)),
            patch.object(ex, "_get_updates", side_effect=fake_updates),
        ):
            outcome = ex.request_approval(req, "tok-orch")

        assert outcome.status == "failed"
        assert "timed out" in (outcome.error or "")

    def test_poll_approval_timeout(self, bot_env: dict[str, str]) -> None:
        ex = TelegramAgentExecutor(
            group_id=GROUP_ID,
            enable_approval_polling=True,
            approval_poll_seconds=1,
            poll_interval=0.1,
        )
        req = _make_request()

        with (
            patch.dict(os.environ, bot_env),
            patch.object(ex, "_send_message", MagicMock(return_value=42)),
            patch.object(ex, "_get_updates", return_value=[]),
        ):
            outcome = ex.request_approval(req, "tok-orch")

        assert outcome.status == "failed"
        assert "timed out" in (outcome.error or "")
        assert outcome.retryable is True


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------


class TestBroadcast:
    def test_broadcast_sends_message(self, bot_env: dict[str, str]) -> None:
        ex = TelegramAgentExecutor(group_id=GROUP_ID)
        with (
            patch.dict(os.environ, bot_env),
            patch.object(ex, "_send_message", MagicMock(return_value=55)) as mock,
        ):
            result = ex.broadcast("architect", "Hello group!")
        assert result == 55
        mock.assert_called_once_with("tok-arch", "Hello group!")

    def test_broadcast_unknown_role(self) -> None:
        ex = TelegramAgentExecutor(group_id=GROUP_ID)
        result = ex.broadcast("nonexistent", "Hello")
        assert result is None


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


class TestCommandConstruction:
    def test_passes_expected_flags(
        self, executor: TelegramAgentExecutor, step_request: StepRequest
    ) -> None:
        captured_cmd: list[str] = []

        def capture_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            if args and isinstance(args[0], list) and args[0][0] == executor.claude_bin:
                captured_cmd.extend(args[0])
                return subprocess.CompletedProcess(args[0], 0, stdout="{}", stderr="")
            return subprocess.CompletedProcess(args[0] if args else [], 0, stdout="", stderr="")

        with (
            patch.dict(os.environ, {"BOT_BACKEND": "tok-be"}),
            patch.object(executor, "_send_message", MagicMock(return_value=42)),
            patch("arvino.adapters.telegram_executor.subprocess.run", side_effect=capture_run),
        ):
            executor.execute(step_request)

        assert "--dangerously-skip-permissions" in captured_cmd
        assert "--output-format" in captured_cmd
        assert "json" in captured_cmd
        assert "--model" in captured_cmd
        assert "sonnet" in captured_cmd
        assert "--allowedTools" in captured_cmd


# ---------------------------------------------------------------------------
# Telegram API interactions (httpx mocking)
# ---------------------------------------------------------------------------


class TestSendMessage:
    def test_send_message_success(self, bot_env: dict[str, str]) -> None:

        ex = TelegramAgentExecutor(group_id=GROUP_ID)

        class MockResponse:
            status_code = 200
            @staticmethod
            def json() -> dict[str, Any]:
                return {"ok": True, "result": {"message_id": 42}}
            @staticmethod
            def raise_for_status() -> None:
                pass

        with patch.dict(os.environ, bot_env), patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value.post.return_value = MockResponse()
            mock_client_cls.return_value = mock_client

            result = ex._send_message("tok-be", "hello")

        assert result == 42

    def test_send_message_api_error(self, bot_env: dict[str, str]) -> None:

        ex = TelegramAgentExecutor(group_id=GROUP_ID)

        class MockResponse:
            status_code = 200
            @staticmethod
            def json() -> dict[str, Any]:
                return {"ok": False, "description": "Forbidden: bot was kicked"}
            @staticmethod
            def raise_for_status() -> None:
                pass

        with patch.dict(os.environ, bot_env), patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value.post.return_value = MockResponse()
            mock_client_cls.return_value = mock_client

            result = ex._send_message("tok-be", "hello")

        assert result is None


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_satisfies_agent_executor_protocol(self) -> None:
        """TelegramAgentExecutor must structurally match AgentExecutor."""
        from arvino.ports import AgentExecutor

        ex: AgentExecutor = TelegramAgentExecutor(group_id=GROUP_ID)  # type: ignore[assignment]
        assert hasattr(ex, "execute")
        assert callable(ex.execute)