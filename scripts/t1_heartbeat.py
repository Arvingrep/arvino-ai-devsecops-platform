#!/usr/bin/env python3
"""T1 ledger heartbeat: lease supervision and dispatch visibility.

Runs without any third-party dependency so it can be executed from the
arvino-api image, from a local shell, or from a Kubernetes CronJob.

What it does, every tick:

1. Reads the active runs through the owner token and builds a lease
   inventory (which step is leased, to whom, when it expires).
2. Reads the dispatch queue through the agent token (``GET /work``) and
   checks the dispatch invariant: every running agent step without a lease
   must be offered to executors.
3. Reclaims leases that expired long enough ago and whose executor is gone,
   using the operator reclaim endpoint with explicit stop evidence.  Lease
   expiry alone is never treated as evidence by the engine, so the heartbeat
   supplies ``operator_declared`` with a factual detail string and only for
   the principals it owns.
4. Writes a small state file atomically so the next tick can diff against it.

It never prints credentials, never mutates the ledger except through the
documented reclaim endpoint, and never touches a business repository.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

USER_AGENT = "curl/8.7.1"
MANAGED_PRINCIPALS = ("agent:harness",)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class Api:
    def __init__(self, base_url: str, timeout: float = 20.0) -> None:
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def call(
        self, method: str, path: str, token: str, body: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Authorization", "Bearer " + token)
        req.add_header("User-Agent", USER_AGENT)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except Exception:
                return exc.code, {"detail": raw.decode(errors="replace")[:200]}
        except Exception as exc:  # network/timeout
            return 0, {"detail": type(exc).__name__}


def collect_leases(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    leases: list[dict[str, Any]] = []
    for run in runs:
        for step in run.get("steps") or []:
            principal = step.get("executor_principal")
            if not principal:
                continue
            leases.append(
                {
                    "run_id": run.get("id"),
                    "step_key": step.get("key"),
                    "status": step.get("status"),
                    "principal": principal,
                    "attempt": step.get("attempt"),
                    "claimed_at": step.get("executor_claimed_at"),
                    "expires_at": step.get("executor_lease_expires_at"),
                }
            )
    return leases


def collect_unleased_running(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for run in runs:
        for step in run.get("steps") or []:
            if step.get("kind") != "agent" or step.get("status") != "running":
                continue
            if step.get("executor_principal"):
                continue
            pending.append(
                {
                    "run_id": run.get("id"),
                    "step_key": step.get("key"),
                    "role": step.get("role"),
                }
            )
    return pending


def atomic_write(path: str, payload: dict[str, Any]) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".t1-heartbeat-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="T1 ledger heartbeat")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("ARVINO_API_URL", "https://arvino-api.k8shome.com"),
    )
    parser.add_argument(
        "--state-file",
        default=os.environ.get(
            "ARVINO_HEARTBEAT_STATE", "/Users/arvin/Documents/arvino/ops-reports/t1-heartbeat.json"
        ),
    )
    parser.add_argument(
        "--reclaim-after-seconds",
        type=int,
        default=int(os.environ.get("ARVINO_HEARTBEAT_RECLAIM_AFTER", "300")),
        help="minimum time after lease expiry before the heartbeat reclaims",
    )
    parser.add_argument(
        "--warn-before-seconds",
        type=int,
        default=int(os.environ.get("ARVINO_HEARTBEAT_WARN_BEFORE", "600")),
        help="report leases that expire within this window",
    )
    parser.add_argument("--dry-run", action="store_true", help="report only, never reclaim")
    args = parser.parse_args()

    owner = os.environ.get("ARVINO_OWNER_TOKEN", "")
    agent = os.environ.get("ARVINO_AGENT_TOKEN", "")
    if not owner or not agent:
        print("heartbeat: missing ARVINO_OWNER_TOKEN / ARVINO_AGENT_TOKEN", file=sys.stderr)
        return 2

    api = Api(args.api_url)
    status, runs = api.call("GET", "/runs?active=true", owner)
    if status != 200 or not isinstance(runs, list):
        print(f"heartbeat: cannot read /runs (HTTP {status})", file=sys.stderr)
        return 3
    runs = [r for r in runs if isinstance(r, dict)]

    work_status, work = api.call("GET", "/work", agent)
    work_ok = work_status == 200 and isinstance(work, list)

    leases = collect_leases(runs)
    unleased = collect_unleased_running(runs)

    now = now_utc()
    expiring_soon: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    for lease in leases:
        expires = parse_ts(lease.get("expires_at"))
        lease["expired_for_seconds"] = None
        if expires is None:
            continue
        remaining = (expires - now).total_seconds()
        if remaining > 0 and remaining <= args.warn_before_seconds:
            lease["expires_in_seconds"] = int(remaining)
            expiring_soon.append(lease)
        elif remaining <= 0:
            lease["expired_for_seconds"] = int(-remaining)
            expired.append(lease)

    work_keys = set()
    if work_ok:
        for item in work:
            if isinstance(item, dict):
                work_keys.add((item.get("run_id"), item.get("step_key") or item.get("key")))

    dispatch_missing = [
        item
        for item in unleased
        if work_ok and (item["run_id"], item["step_key"]) not in work_keys
    ]

    actions: list[dict[str, Any]] = []
    for lease in expired:
        if lease.get("principal") not in MANAGED_PRINCIPALS:
            continue
        if (lease.get("expired_for_seconds") or 0) < args.reclaim_after_seconds:
            continue
        detail = (
            f"lease expired at {lease.get('expires_at')} "
            f"({lease.get('expired_for_seconds')}s ago) for step {lease.get('step_key')!r}; "
            f"executor principal {lease.get('principal')!r} produced no renewal; "
            "T1 ledger heartbeat requeues the step for the next executor"
        )
        if args.dry_run:
            actions.append({"action": "would_reclaim", **lease})
            continue
        code, body = api.call(
            "POST",
            f"/runs/{lease['run_id']}/steps/{lease['step_key']}/reclaim",
            owner,
            {"stop_evidence": "operator_declared", "detail": detail},
        )
        actions.append({"action": "reclaim", "http": code, **lease})

    report = {
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "api_url": args.api_url,
        "runs_active": len(runs),
        "leases_active": len(leases),
        "leases_expiring_soon": [
            {"run_id": l["run_id"], "step_key": l["step_key"], "expires_in_seconds": l.get("expires_in_seconds")}
            for l in expiring_soon
        ],
        "leases_expired": [
            {
                "run_id": l["run_id"],
                "step_key": l["step_key"],
                "principal": l["principal"],
                "expired_for_seconds": l.get("expired_for_seconds"),
            }
            for l in expired
        ],
        "dispatch_queue_depth": len(work) if work_ok else None,
        "dispatch_invariant_violations": dispatch_missing,
        "actions": actions,
    }
    try:
        atomic_write(args.state_file, report)
    except Exception as exc:
        print(f"heartbeat: cannot write state file ({type(exc).__name__})", file=sys.stderr)

    summary = {
        "checked_at": report["checked_at"],
        "runs_active": report["runs_active"],
        "leases_active": report["leases_active"],
        "leases_expiring_soon": len(expiring_soon),
        "leases_expired": len(expired),
        "dispatch_queue_depth": report["dispatch_queue_depth"],
        "dispatch_invariant_violations": len(dispatch_missing),
        "actions": len(actions),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
