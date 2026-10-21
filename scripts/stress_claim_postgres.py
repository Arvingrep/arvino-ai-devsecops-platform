#!/usr/bin/env python3
"""Concurrency stress for the step-lease CAS on a real Postgres.

SQLite cannot model two writers; this script exercises the Postgres row locks
and the repo_write_locks primary key with real threads.

Usage:
    DATABASE_URL=postgresql+psycopg://postgres:stress@127.0.0.1:55432/arvino \
        PYTHONPATH=src python scripts/stress_claim_postgres.py --workers 16

The target database must be disposable: the script initialises the schema and
creates its own project/issue/runs.  It never touches production.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import uuid
from collections import Counter
from typing import Any

from arvino.db import init_schema, make_engine, make_session_factory
from arvino.domain import ConflictError, Principal, PrincipalKind, Profile
from arvino.engine import Engine, EngineSettings
from arvino.fakes import (
    FakeGit,
    RecordingNotifier,
    RecordingTracker,
    RecordOnlyPromoter,
    ScriptedExecutor,
    StaticPolicy,
)
from arvino.ports import Ports, StepOutcome
from arvino.workflows.model import load_registry

REPO = "acme/lease-stress"


def build(database_url: str) -> Engine:
    sa = make_engine(database_url)
    init_schema(sa)
    executor = ScriptedExecutor()
    executor.script("develop", StepOutcome(status="running"))
    return Engine(
        make_session_factory(sa),
        load_registry(),
        Ports(
            executor=executor,
            tracker=RecordingTracker(),
            git=FakeGit(),
            promoter=RecordOnlyPromoter(),
            policy=StaticPolicy(allow=True),
            notifier=RecordingNotifier(),
        ),
        settings=EngineSettings(),
    )


def make_run(engine: Engine, project_id: str, issue_id: str, key: str) -> str:
    view, _ = engine.start_run(project_id, issue_id, Profile.FAST, key, "owner")
    assert view.step("develop").status == "running", view.statuses()
    return view.id


def dev(i: int) -> Principal:
    return Principal(
        id=f"agent:developer-{i}", kind=PrincipalKind.AGENT, roles=("developer",)
    )


def race(engine: Engine, claims: list[tuple[str, Principal]], workers: int) -> list[dict[str, Any]]:
    """Fire ``workers`` claims at each target simultaneously; return outcomes."""
    barrier = threading.Barrier(workers)
    results: list[dict[str, Any]] = []
    lock = threading.Lock()

    def one(idx: int) -> None:
        run_id, principal = claims[idx]
        barrier.wait()
        try:
            lease = engine.claim_step(run_id, "develop", principal, ttl_seconds=60)
            with lock:
                results.append(
                    {
                        "kind": "ok",
                        "run_id": run_id,
                        "principal": principal.id,
                        "token": lease.lease_token,
                    }
                )
        except ConflictError as exc:
            with lock:
                results.append({"kind": "conflict", "run_id": run_id, "detail": str(exc)})
        except Exception as exc:  # pragma: no cover - surfaced as a failure
            with lock:
                results.append({"kind": "error", "run_id": run_id, "detail": repr(exc)})

    threads = [threading.Thread(target=one, args=(i,)) for i in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    if args.workers < 2:
        parser.error("--workers must be >= 2")

    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        print("DATABASE_URL must point at a disposable Postgres database", file=sys.stderr)
        return 2

    engine = build(url)
    suffix = uuid.uuid4().hex[:8]
    project = engine.create_project(
        f"lease-stress-{suffix}", "Lease stress", repo_full_name=REPO
    )
    issue, _ = engine.upsert_issue(project.id, "plane", f"STRESS-{suffix}", "stress")

    run_id = make_run(engine, project.id, issue.id, f"race-{suffix}")
    workers = args.workers
    results = race(engine, [(run_id, dev(i)) for i in range(workers)], workers)
    winners = [r for r in results if r["kind"] == "ok"]
    conflicts = [r for r in results if r["kind"] == "conflict"]
    errors = [r for r in results if r["kind"] == "error"]

    print(f"same-step race: workers={workers} winners={len(winners)} conflicts={len(conflicts)}")
    if errors:
        for err in errors:
            print("  error:", err["detail"], file=sys.stderr)
        return 1
    if len(winners) != 1:
        print(f"  expected exactly 1 winner, got {len(winners)}", file=sys.stderr)
        return 1

    claimed = [e for e in engine.events(run_id) if e.type == "step.lease_claimed"]
    rejected = [e for e in engine.events(run_id) if e.type == "step.lease_claim_rejected"]
    reasons = Counter(str(e.payload.get("reason")) for e in rejected)
    print(f"  audit: claimed={len(claimed)} rejected={len(rejected)} reasons={dict(reasons)}")
    if len(claimed) != 1 or len(rejected) != workers - 1:
        print("  audit counts do not match the race outcome", file=sys.stderr)
        return 1
    if set(reasons) - {"already_leased"}:
        print(f"  unexpected rejection reasons: {dict(reasons)}", file=sys.stderr)
        return 1

    winner = winners[0]
    engine.release_lease(
        run_id, "develop", str(winner["token"]), dev(int(winner["principal"].rsplit("-", 1)[1]))
    )

    # Two different runs, same repo: the repo mutex must admit exactly one.
    run_a = make_run(engine, project.id, issue.id, f"a-{suffix}")
    run_b = make_run(engine, project.id, issue.id, f"b-{suffix}")
    pair = race(
        engine,
        [(run_a, dev(100)), (run_b, dev(101))],
        2,
    )
    ok_pair = [r for r in pair if r["kind"] == "ok"]
    bad_pair = [r for r in pair if r["kind"] != "ok"]
    print(f"same-repo race: winners={len(ok_pair)} conflicts={len(bad_pair)}")
    for item in bad_pair:
        print(f"  loser: {item['detail']}")
    if len(ok_pair) != 1 or len(bad_pair) != 1:
        print("  expected exactly one repo-lock winner", file=sys.stderr)
        return 1
    if "write-locked" not in bad_pair[0]["detail"]:
        print("  loser did not fail on the repo write lock", file=sys.stderr)
        return 1

    print("stress: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
