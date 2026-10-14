"""Webhook authentication. Missing secret configuration always means *reject*."""

from __future__ import annotations

import hashlib
import hmac


class WebhookAuthError(Exception):
    """Authentication failed (maps to 401). Never includes secret material."""


def verify_hmac_sha256(
    secret: str | None, body: bytes, header: str | None, *, prefix: str = ""
) -> None:
    if not secret:
        raise WebhookAuthError("webhook secret not configured")
    if not header:
        raise WebhookAuthError("missing signature header")
    got = header[len(prefix) :] if prefix and header.startswith(prefix) else header
    if prefix and not header.startswith(prefix):
        raise WebhookAuthError("malformed signature header")
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, got.strip().lower()):
        raise WebhookAuthError("signature mismatch")


def verify_shared_token(secret: str | None, header: str | None) -> None:
    if not secret:
        raise WebhookAuthError("webhook token not configured")
    if not header:
        raise WebhookAuthError("missing token header")
    if not hmac.compare_digest(
        hashlib.sha256(secret.encode()).digest(), hashlib.sha256(header.encode()).digest()
    ):
        raise WebhookAuthError("token mismatch")


def sign(secret: str, body: bytes) -> str:
    """Helper for tests and the smoke script."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
