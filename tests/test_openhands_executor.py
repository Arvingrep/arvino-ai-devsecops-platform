"""Tests for the OpenHands executor adapter.

All HTTP calls are mocked via ``unittest.mock``; no real network or
OpenHands instance is required.  The tests mirror the structure of
``test_claude_code_executor.py`` and verify:

- Goal construction (prompt content, guardrails, artifacts)
- Conversation lifecycle (create → goal → poll → response)
- Terminal-status detection and StepOutcome mapping
- Error-status and HTTP-error handling
- API-key guard
- PR-number extraction
- Protocol compliance with AgentExecutor
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from arvino.adapters.openhands import OpenHandsExecutor, OpenHandsError
from arvino.domain import ArtifactSpec
from arvino.ports import IssueRef, StepOutcome, StepRequest


# ---------------------------------------------------------------------------
# Helpers
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


def _mock_response(status_code: int = 200, json_body: Any = None) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.is_error = status_code >= 400
    resp.json.return_value = json_body or {}
    resp.text = str(json_body or "")
    return resp


# ---------------------------------------------------------------------------
# Goal building
# ---------------------------------------------------------------------------


class TestBuildGoal:
    def test_contains_role_and_issue(self) -> None:
        ex = OpenHandsExecutor()
        req = _make_request()
        goal = ex._build_goal(req)
        assert "**developer**" in goal
        assert "Add user login" in goal
        assert "OAuth2 login flow" in goal

    def test_contains_branch_prefix_guardrail(self) -> None:
        ex = OpenHandsExecutor()
        req = _make_request(branch_prefix="arvino/run-x/")
        goal = ex._build_goal(req)
        assert "arvino/run-x/" in goal
        assert "Never merge" in goal

    def test_includes_upstream_artifacts(self) -> None:
        ex = OpenHandsExecutor()
        art = ArtifactSpec(
            kind="note",
            name="triage-result",
            uri="git://fake/run-001/triage",
            metadata={"foo": "bar"},
        )
        req = _make_request(upstream_artifacts=[art])
        goal = ex._build_goal(req)
        assert "triage-result" in goal
        assert "git://fake/run-001/triage" in goal
        assert '"foo"' in goal

    def test_omits_empty_body(self) -> None:
        ex = OpenHandsExecutor()
        req = _make_request(
            issue=IssueRef(id="i", source="gh", external_id="1", title="T", body=""),
        )
        goal = ex._build_goal(req)
        assert "## Issue: T" in goal

    def test_contains_target_repo(self) -> None:
        ex = OpenHandsExecutor()
        req = _make_request(repo="org/repo")
        goal = ex._build_goal(req)
        assert "org/repo" in goal


# ---------------------------------------------------------------------------
# PR number extraction
# ---------------------------------------------------------------------------


class TestExtractPrNumber:
    def test_hash_pattern(self) -> None:
        assert OpenHandsExecutor._extract_pr_number({"content": "Opened PR #42"}) == 42

    def test_pull_slash_pattern(self) -> None:
        assert OpenHandsExecutor._extract_pr_number({"result": "See pull/99"}) == 99

    def test_pull_request_spelled_out(self) -> None:
        assert OpenHandsExecutor._extract_pr_number({"message": "Created pull request 7"}) == 7

    def test_no_pr(self) -> None:
        assert OpenHandsExecutor._extract_pr_number({"content": "Done!"}) is None
        assert OpenHandsExecutor._extract_pr_number({}) is None


# ---------------------------------------------------------------------------
# API-key guard
# ---------------------------------------------------------------------------


class TestApiKeyGuard:
    def test_missing_key_returns_failed_non_retryable(self) -> None:
        ex = OpenHandsExecutor()
        req = _make_request()
        env = {k: v for k, v in os.environ.items() if k != "OPENHANDS_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            outcome = ex.execute(req)
        assert outcome.status == "failed"
        assert "OPENHANDS_API_KEY" in (outcome.error or "")
        assert outcome.retryable is False


# ---------------------------------------------------------------------------
# Happy path (mocked httpx.Client)
# ---------------------------------------------------------------------------


def _build_client_mock(
    *,
    conv_id: str = "conv-abc",
    final_status: str = "completed",
    final_response: dict[str, Any] | None = None,
    git_diff: dict[str, Any] | None = None,
    poll_steps: int = 1,
) -> MagicMock:
    """Return a mock httpx.Client whose responses simulate a successful run."""
    client = MagicMock()

    # POST /api/conversations → create
    client.post.side_effect = [
        _mock_response(200, {"id": conv_id}),           # create conversation
        _mock_response(200, {"started": True}),          # start goal
    ]

    # GET /api/conversations/{id} → poll (running N-1 times, then terminal)
    running = _mock_response(200, {"status": "running"})
    done = _mock_response(200, {"status": final_status})
    polls = [running] * (poll_steps - 1) + [done]

    # GET /api/conversations/{id}/agent_final_response
    final = _mock_response(200, final_response or {"content": "All done."})
    # GET /api/conversations/{id}/git/diff
    diff = _mock_response(200, git_diff or {"diff": ""})

    client.get.side_effect = polls + [final, diff]

    return client


class TestExecuteHappyPath:
    def test_succeeds_with_note_artifact(self) -> None:
        ex = OpenHandsExecutor(poll_interval=0)
        req = _make_request()

        client_mock = _build_client_mock(conv_id="c1")

        with (
            patch.dict(os.environ, {"OPENHANDS_API_KEY": "key-secret"}),
            patch("arvino.adapters.openhands.httpx.Client") as MockClient,
        ):
            MockClient.return_value.__enter__ = lambda s: client_mock
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            outcome = ex.execute(req)

        assert outcome.status == "succeeded"
        assert outcome.actor == "agent:developer"
        assert any(a.kind == "note" for a in outcome.artifacts)
        note = next(a for a in outcome.artifacts if a.kind == "note")
        assert "develop" in note.name
        assert "c1" in note.metadata["conversation_id"]

    def test_pr_artifact_when_pr_mentioned(self) -> None:
        ex = OpenHandsExecutor(poll_interval=0)
        req = _make_request()

        client_mock = _build_client_mock(
            conv_id="c2",
            final_response={"content": "Created pull request #7 for the feature."},
        )

        with (
            patch.dict(os.environ, {"OPENHANDS_API_KEY": "key-secret"}),
            patch("arvino.adapters.openhands.httpx.Client") as MockClient,
        ):
            MockClient.return_value.__enter__ = lambda s: client_mock
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            outcome = ex.execute(req)

        assert outcome.status == "succeeded"
        pr_arts = [a for a in outcome.artifacts if a.kind == "pull-request"]
        assert len(pr_arts) == 1
        assert pr_arts[0].metadata["number"] == 7
        assert "acme/app" in pr_arts[0].uri

    def test_output_includes_conversation_id_and_diff(self) -> None:
        ex = OpenHandsExecutor(poll_interval=0)
        req = _make_request()

        client_mock = _build_client_mock(
            conv_id="c3",
            git_diff={"diff": "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-x\n+y"},
        )

        with (
            patch.dict(os.environ, {"OPENHANDS_API_KEY": "key-secret"}),
            patch("arvino.adapters.openhands.httpx.Client") as MockClient,
        ):
            MockClient.return_value.__enter__ = lambda s: client_mock
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            outcome = ex.execute(req)

        assert outcome.output["conversation_id"] == "c3"
        assert "diff" in outcome.output["git_diff"]

    def test_polls_multiple_times_before_terminal(self) -> None:
        ex = OpenHandsExecutor(poll_interval=0)
        req = _make_request()

        client_mock = _build_client_mock(conv_id="c4", poll_steps=3)

        with (
            patch.dict(os.environ, {"OPENHANDS_API_KEY": "key-secret"}),
            patch("arvino.adapters.openhands.httpx.Client") as MockClient,
        ):
            MockClient.return_value.__enter__ = lambda s: client_mock
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            outcome = ex.execute(req)

        assert outcome.status == "succeeded"
        # 3 poll calls + 1 final_response + 1 git_diff = 5 GET calls
        assert client_mock.get.call_count == 5


# ---------------------------------------------------------------------------
# Error / failure cases
# ---------------------------------------------------------------------------


class TestExecuteErrors:
    def test_openhands_error_status_returns_failed_retryable(self) -> None:
        ex = OpenHandsExecutor(poll_interval=0)
        req = _make_request()

        client_mock = _build_client_mock(
            conv_id="err1",
            final_status="error",
            final_response={"content": "Something went wrong"},
        )

        with (
            patch.dict(os.environ, {"OPENHANDS_API_KEY": "key-secret"}),
            patch("arvino.adapters.openhands.httpx.Client") as MockClient,
        ):
            MockClient.return_value.__enter__ = lambda s: client_mock
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            outcome = ex.execute(req)

        assert outcome.status == "failed"
        assert outcome.retryable is True

    def test_http_error_on_create_returns_failed(self) -> None:
        ex = OpenHandsExecutor(poll_interval=0)
        req = _make_request()

        client_mock = MagicMock()
        client_mock.post.return_value = _mock_response(500, {"detail": "Internal Server Error"})

        with (
            patch.dict(os.environ, {"OPENHANDS_API_KEY": "key-secret"}),
            patch("arvino.adapters.openhands.httpx.Client") as MockClient,
        ):
            MockClient.return_value.__enter__ = lambda s: client_mock
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            outcome = ex.execute(req)

        assert outcome.status == "failed"
        assert outcome.retryable is True
        assert "500" in (outcome.error or "")

    def test_http_timeout_returns_failed_retryable(self) -> None:
        ex = OpenHandsExecutor(poll_interval=0, timeout_seconds=60)
        req = _make_request()

        client_mock = MagicMock()
        client_mock.post.side_effect = httpx.ConnectTimeout("timed out")

        with (
            patch.dict(os.environ, {"OPENHANDS_API_KEY": "key-secret"}),
            patch("arvino.adapters.openhands.httpx.Client") as MockClient,
        ):
            MockClient.return_value.__enter__ = lambda s: client_mock
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            outcome = ex.execute(req)

        assert outcome.status == "failed"
        assert "timed out" in (outcome.error or "").lower()
        assert outcome.retryable is True

    def test_wall_clock_timeout_raises_openhands_error(self) -> None:
        """When the agent keeps running past timeout_seconds, poll raises."""
        ex = OpenHandsExecutor(poll_interval=0, timeout_seconds=1)
        req = _make_request()

        # Simulate conversation that never leaves "running".
        client_mock = MagicMock()
        always_running = _mock_response(200, {"status": "running"})
        client_mock.post.side_effect = [
            _mock_response(200, {"id": "c-timeout"}),
            _mock_response(200, {}),
        ]
        client_mock.get.return_value = always_running

        with (
            patch.dict(os.environ, {"OPENHANDS_API_KEY": "key-secret"}),
            patch("arvino.adapters.openhands.httpx.Client") as MockClient,
            # Freeze time so deadline is hit after just a couple of poll iterations.
            patch("arvino.adapters.openhands.time.monotonic", side_effect=[0.0, 0.0, 999.0, 999.0]),
            patch("arvino.adapters.openhands.time.sleep"),
        ):
            MockClient.return_value.__enter__ = lambda s: client_mock
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            outcome = ex.execute(req)

        assert outcome.status == "failed"
        assert "terminal" in (outcome.error or "").lower() or "timed out" in (outcome.error or "").lower()

    def test_404_on_final_response_still_succeeds(self) -> None:
        """Missing final response (404) should not fail the step."""
        ex = OpenHandsExecutor(poll_interval=0)
        req = _make_request()

        client_mock = MagicMock()
        client_mock.post.side_effect = [
            _mock_response(200, {"id": "c-nofinal"}),
            _mock_response(200, {}),
        ]
        not_found = _mock_response(404, {})
        diff_ok = _mock_response(200, {})
        client_mock.get.side_effect = [
            _mock_response(200, {"status": "completed"}),
            not_found,   # agent_final_response → 404
            diff_ok,
        ]

        with (
            patch.dict(os.environ, {"OPENHANDS_API_KEY": "key-secret"}),
            patch("arvino.adapters.openhands.httpx.Client") as MockClient,
        ):
            MockClient.return_value.__enter__ = lambda s: client_mock
            MockClient.return_value.__exit__ = MagicMock(return_value=False)
            outcome = ex.execute(req)

        assert outcome.status == "succeeded"


# ---------------------------------------------------------------------------
# _raise_for_status
# ---------------------------------------------------------------------------


class TestRaiseForStatus:
    def test_2xx_does_not_raise(self) -> None:
        resp = _mock_response(200)
        OpenHandsExecutor._raise_for_status(resp, "op")  # should not raise

    def test_5xx_raises_openhands_error(self) -> None:
        resp = _mock_response(503, {"detail": "unavailable"})
        with pytest.raises(OpenHandsError, match="503"):
            OpenHandsExecutor._raise_for_status(resp, "poll conversation")


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_satisfies_agent_executor_protocol(self) -> None:
        from arvino.ports import AgentExecutor

        ex: AgentExecutor = OpenHandsExecutor()  # type: ignore[assignment]
        assert hasattr(ex, "execute")
        assert callable(ex.execute)
