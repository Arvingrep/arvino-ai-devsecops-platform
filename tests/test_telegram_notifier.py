"""Tests for the Telegram Run Notifier adapter."""

from __future__ import annotations

import logging
import urllib.error
from unittest.mock import MagicMock, patch

from arvino.adapters.telegram_notifier import TelegramRunNotifier
from arvino.ports import IssueRef

# -- Test 1: no token -> no-op (no HTTP call made) ----------------------------


def test_no_token_no_http_call() -> None:
    notifier = TelegramRunNotifier(bot_token=None, chat_id=None)
    with patch("urllib.request.urlopen") as mock_urlopen:
        notifier.post_status(
            IssueRef(id="1", source="gh", external_id="42", title="test"), "succeeded: done"
        )
        mock_urlopen.assert_not_called()


# -- Test 2: post_status constructs correct emoji-prefixed message -------------


def test_post_status_emoji_prefix() -> None:
    notifier = TelegramRunNotifier(bot_token="tok", chat_id="123")
    captured: list[str] = []

    def fake_send(text: str) -> None:
        captured.append(text)

    with patch.object(notifier, "_send", side_effect=fake_send):
        notifier.post_status(
            IssueRef(id="1", source="gh", external_id="42", title="test"), "succeeded: all good"
        )
    assert len(captured) == 1
    assert captured[0].startswith("\u2705")  # checkmark
    assert "<b>Arvino</b>" in captured[0]
    assert "succeeded: all good" in captured[0]


# -- Test 3: post_status detects waiting_approval -----------------------------


def test_post_status_detects_waiting_approval() -> None:
    notifier = TelegramRunNotifier(bot_token="tok", chat_id="123")
    captured: list[str] = []

    def fake_send(text: str) -> None:
        captured.append(text)

    with patch.object(notifier, "_send", side_effect=fake_send):
        notifier.post_status(
            IssueRef(id="1", source="gh", external_id="42", title="test"),
            "waiting_approval: run needs owner sign-off",
        )
    assert len(captured) == 1
    assert captured[0].startswith("\u23f3")  # hourglass
    assert "waiting_approval" in captured[0]


# -- Test 4: poke forwards to underlying poke_fn -------------------------------


def test_poke_forwards() -> None:
    poke_fn = MagicMock()
    notifier = TelegramRunNotifier(bot_token=None, chat_id=None, poke_fn=poke_fn)
    notifier.poke("run-42")
    poke_fn.assert_called_once_with("run-42")


# -- Test 5: poke does not fail when poke_fn is None ---------------------------


def test_poke_noop_when_no_poke_fn() -> None:
    notifier = TelegramRunNotifier(bot_token=None, chat_id=None, poke_fn=None)
    # Should not raise
    notifier.poke("run-42")


# -- Test 6: HTTP failure is caught and logged, never raised ------------------


def test_http_failure_caught_and_logged() -> None:
    import urllib.request as urllib_request

    notifier = TelegramRunNotifier(bot_token="tok", chat_id="123")

    # Directly mock the module's logger to verify the warning is emitted
    with (
        patch.object(logging.getLogger("arvino.adapters.telegram_notifier"), "warning") as mock_warning,
        patch.object(urllib_request, "urlopen", side_effect=urllib.error.URLError("connection refused")),
    ):
        # Should not raise
        notifier._send("test message")

    # Verify the warning was logged
    mock_warning.assert_called_once()
    call_msg = mock_warning.call_args[0][0]
    assert "telegram send failed" in call_msg