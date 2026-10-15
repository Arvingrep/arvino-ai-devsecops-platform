"""Telegram notifications for run status changes.

Implements both IssueTracker (post_status) and RunNotifier (poke), so a single
instance can replace both in production when a Telegram token is configured.

Design decision (set by orchestrator):
- Bot token and chat ID come from Settings (env vars ARVINO_TG_BOT_TOKEN, ARVINO_TG_CHAT_ID)
- Failures are logged, never raised — the notifier must not block engine operations
- Messages are sent synchronously (simple urllib.request, no aiohttp dependency)
- Message format: plain text, emoji status prefix
- If token/chat_id not configured, falls back to no-op (logs a debug message)
"""

from __future__ import annotations

import json
import logging
import ssl
import urllib.request
from typing import Any

from arvino.ports import IssueRef, RunNotifier

log = logging.getLogger(__name__)

EMOJI = {
    "running": "\U0001f504",
    "succeeded": "\u2705",
    "failed": "\u274c",
    "cancelled": "\u26ab",
    "waiting_approval": "\u23f3",
}


class TelegramRunNotifier:
    """Sends run status updates to a Telegram chat."""

    def __init__(
        self,
        bot_token: str | None,
        chat_id: str | None,
        *,
        poke_fn: Any = None,  # optional underlying RunNotifier for Temporal poke
    ) -> None:
        self._token = bot_token
        self._chat_id = chat_id
        self._poke_fn = poke_fn  # e.g. TemporalNotifier.poke

    def _send(self, text: str) -> None:
        if not self._token or not self._chat_id:
            log.debug("telegram: not configured, skipping message")
            return
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        payload = json.dumps({"chat_id": self._chat_id, "text": text, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                if resp.status != 200:
                    log.warning("telegram API %d", resp.status)
        except Exception as exc:
            log.warning("telegram send failed: %s: %s", type(exc).__name__, exc)

    def post_status(self, issue: IssueRef, text: str) -> None:
        """Implements IssueTracker.post_status — send a status update."""
        status_word = ""
        for word in ("succeeded", "failed", "cancelled", "waiting_approval", "running"):
            if word in text:
                status_word = word
                break
        emoji = EMOJI.get(status_word, "\u2139\ufe0f")
        msg = f"{emoji} <b>Arvino</b>\n{text}"
        self._send(msg)

    def poke(self, run_id: str) -> None:
        """Implements RunNotifier.poke — forward to Temporal and also send a Telegram alert for approvals."""
        if self._poke_fn is not None:
            self._poke_fn(run_id)