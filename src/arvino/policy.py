"""Policy clients (OPA) and fail-closed wrapper."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx

from arvino.ports import Decision, PolicyClient

log = logging.getLogger(__name__)

DECISION_QUERY = "data.arvino.authz.decision"
DECISION_PATH = "arvino/authz/decision"


def parse_result(result: Any) -> Decision:
    """Strict parse. Anything unexpected is a denial (fail closed)."""
    if not isinstance(result, dict):
        return Decision(allow=False, reasons=["policy returned no decision"])
    allow = result.get("allow")
    deny = result.get("deny", [])
    if allow is not True or not isinstance(deny, list) or deny:
        reasons = [str(d) for d in deny] if isinstance(deny, list) else ["malformed deny list"]
        return Decision(allow=False, reasons=reasons or ["policy did not allow"])
    return Decision(allow=True)


class FailClosedPolicy:
    """Wraps any PolicyClient: exceptions become denials."""

    def __init__(self, inner: PolicyClient) -> None:
        self._inner = inner

    def decide(self, policy_input: dict[str, Any]) -> Decision:
        try:
            return self._inner.decide(policy_input)
        except Exception as exc:
            log.error("policy evaluation failed, denying: %s", type(exc).__name__)
            return Decision(allow=False, reasons=[f"policy unavailable: {type(exc).__name__}"])


class OpaHttpPolicy:
    def __init__(
        self, base_url: str, timeout: float = 3.0, client: httpx.Client | None = None
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/v1/data/{DECISION_PATH}"
        self._client = client or httpx.Client(timeout=timeout)

    def decide(self, policy_input: dict[str, Any]) -> Decision:
        resp = self._client.post(self._url, json={"input": policy_input})
        resp.raise_for_status()
        return parse_result(resp.json().get("result"))


class OpaCliPolicy:
    """Evaluates with the `opa` binary. For tests/offline use (one process per call)."""

    def __init__(self, policy_dir: Path, opa_bin: str | None = None, timeout: float = 20.0) -> None:
        found = opa_bin or shutil.which("opa")
        if not found:
            raise FileNotFoundError("opa binary not found")
        self._bin = found
        self._dir = policy_dir
        self._timeout = timeout

    def decide(self, policy_input: dict[str, Any]) -> Decision:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell, input via stdin
            [self._bin, "eval", "-d", str(self._dir), "-I", "-f", "json", DECISION_QUERY],
            input=json.dumps(policy_input),
            capture_output=True,
            text=True,
            timeout=self._timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"opa eval failed rc={proc.returncode}")
        out = json.loads(proc.stdout)
        results = out.get("result") or []
        if not results:
            return Decision(allow=False, reasons=["policy returned no decision"])
        return parse_result(results[0]["expressions"][0]["value"])
