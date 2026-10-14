"""Operator CLI: ``arvino validate-workflows | reconcile | list-runs``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from arvino.bootstrap import build_engine
from arvino.domain import InvalidWorkflowError
from arvino.settings import Settings
from arvino.workflows.model import load_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arvino")
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser(
        "validate-workflows", help="validate workflow definitions (DAG + safety lint)"
    )
    v.add_argument("directory", nargs="?", type=Path, help="defaults to the built-in definitions")
    sub.add_parser("reconcile", help="reap timeouts and advance every active run (recovery)")
    sub.add_parser("list-runs", help="list non-terminal runs")
    sub.add_parser("worker", help="run the Temporal worker (requires ARVINO_TEMPORAL_ADDRESS)")
    args = parser.parse_args(argv)

    if args.cmd == "validate-workflows":
        try:
            reg = load_registry(args.directory)
        except InvalidWorkflowError as exc:
            print(f"INVALID: {exc}", file=sys.stderr)
            return 1
        for w in reg.all():
            print(f"ok {w.profile.value:9} steps={len(w.steps):2} version={w.version}")
        return 0

    settings = Settings()
    engine = build_engine(settings)
    if args.cmd == "worker":
        import asyncio

        from arvino.temporal_worker import run_worker

        if not settings.temporal_address:
            print("ARVINO_TEMPORAL_ADDRESS is not set", file=sys.stderr)
            return 2
        asyncio.run(
            run_worker(
                engine,
                settings.temporal_address,
                settings.temporal_namespace,
                settings.temporal_task_queue,
            )
        )
        return 0
    if args.cmd == "reconcile":
        print(json.dumps({"advanced": engine.reconcile()}))
        return 0
    for run in engine.list_runs(active_only=True):
        print(f"{run.id} {run.profile:9} {run.status:17} {run.updated_at.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
