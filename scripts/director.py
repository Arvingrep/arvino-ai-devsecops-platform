#!/usr/bin/env python3
"""Director Controller — task registry, dependency dispatch, agent monitoring.

Extends the heartbeat monitor into a management layer that:
  - Maintains a task registry (YAML/JSON) with dependency tracking
  - Dispatches tasks respecting dependency order and a 10-slot cap
  - Monitors agent transcripts, detects stuck/failed agents, retries
  - Outputs dispatch-queue.json for the Hermes runtime to consume

Pure stdlib — no external dependencies.

Usage:
    python3 scripts/director.py run --tasks tasks.yaml
    python3 scripts/director.py status
    python3 scripts/director.py add --goal '...' --context '...'
"""

from __future__ import annotations

import argparse
import copy
import glob
import json
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
POLL_INTERVAL = 15  # seconds
STUCK_THRESHOLD = 180  # 3 min no transcript progress → stuck
MAX_SLOTS = 10
DEFAULT_MAX_RETRIES = 2
DEFAULT_TIMEOUT = 600  # 10 min

SCRATCH_DIR = Path.home() / ".hermes" / "cache" / "scratch"
LIVE_DIR = Path.home() / ".hermes" / "cache" / "delegation" / "live"
STATUS_PATH = SCRATCH_DIR / "director-status.json"
LOG_PATH = SCRATCH_DIR / "director-events.log"
DISPATCH_QUEUE_PATH = SCRATCH_DIR / "dispatch-queue.json"
TRANSCRIPT_GLOB = "deleg_*/task-*.log"

# Valid task statuses
VALID_STATUSES = {"pending", "dispatched", "running", "completed", "failed", "stuck"}
TERMINAL_STATUSES = {"completed", "failed"}
ACTIVE_STATUSES = {"dispatched", "running", "stuck"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _elapsed_human(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"


def _read_last_line(path: str) -> tuple[str, int]:
    """Return (last_non_empty_line, total_line_count) cheaply."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return ("", 0)
            f.seek(0)
            line_count = sum(1 for _ in f)
            f.seek(0, 2)
            pos = size - 1
            lines_found = 0
            while pos > 0 and lines_found < 3:
                f.seek(pos)
                if f.read(1) == b"\n":
                    lines_found += 1
                pos -= 1
            last_chunk = f.read().decode("utf-8", errors="replace").strip()
            last_line = last_chunk.split("\n")[-1] if last_chunk else ""
            return (last_line, line_count)
    except (OSError, ValueError):
        return ("", 0)


def _detect_terminal_status(last_line: str) -> Optional[str]:
    """If the transcript shows a final status, return it."""
    ll = last_line.lower()
    if "status=completed" in ll or "exit_reason=completed" in ll:
        return "completed"
    if "status=stopped" in ll or "exit_reason=stopped" in ll:
        return "failed"
    if "status=failed" in ll or "exit_reason=failed" in ll:
        return "failed"
    return None


def _log_event(event: str, subject: str, detail: str = "",
               log_path: Path = LOG_PATH) -> None:
    """Append a single event line to the log file."""
    ts = _now_iso()
    line = f"{ts}  {event:<14}  {subject}"
    if detail:
        line += f"  {detail}"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _load_yaml_or_json(path: Path) -> list[dict[str, Any]]:
    """Load a YAML or JSON task file. YAML support uses a minimal parser
    since we stay stdlib-only; falls back to JSON."""
    text = path.read_text()
    # Try JSON first
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "tasks" in data:
            return data["tasks"]
        if isinstance(data, list):
            return data
        return [data]
    except json.JSONDecodeError:
        pass
    # Minimal YAML-subset parser for task lists
    return _parse_simple_yaml(text)


def _parse_simple_yaml(text: str) -> list[dict[str, Any]]:
    """Parse a minimal YAML subset: list of flat dicts with string/int/list values.
    Handles the task registry format without pulling in PyYAML."""
    tasks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # New list item
        if stripped.startswith("- "):
            if current is not None:
                tasks.append(current)
            current = {}
            stripped = stripped[2:].strip()
            if not stripped:
                continue
        if current is None:
            # Top-level key like "tasks:" — skip
            if stripped.endswith(":"):
                continue
            current = {}
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            # Remove quotes
            if (val.startswith('"') and val.endswith('"')) or \
               (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            # Inline list [a, b, c]
            if val.startswith("[") and val.endswith("]"):
                items = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
                current[key] = items
            # Integer
            elif val.isdigit():
                current[key] = int(val)
            # Boolean
            elif val.lower() in ("true", "false"):
                current[key] = val.lower() == "true"
            # null / empty
            elif val.lower() == "null" or val == "~" or val == "":
                current[key] = None
            else:
                current[key] = val
    if current is not None:
        tasks.append(current)
    return tasks


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------

@dataclass
class Task:
    """A single task in the registry."""
    task_id: str
    goal: str
    context: str = ""
    dependencies: list[str] = field(default_factory=list)
    status: str = "pending"
    agent_id: Optional[str] = None
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_count: int = 0
    timeout_seconds: int = DEFAULT_TIMEOUT
    # Runtime fields
    dispatched_at: Optional[str] = None
    completed_at: Optional[str] = None
    fail_reason: Optional[str] = None
    last_line_count: int = 0
    last_change_ts: float = 0.0
    transcript_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Drop internal runtime fields from serialization
        for k in ("last_line_count", "last_change_ts", "transcript_path"):
            d.pop(k, None)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Task:
        # Normalize field names
        tid = d.get("task_id") or d.get("id") or str(uuid.uuid4())[:8]
        return cls(
            task_id=str(tid),
            goal=d.get("goal", ""),
            context=d.get("context", ""),
            dependencies=d.get("dependencies") or [],
            status=d.get("status", "pending"),
            agent_id=d.get("agent_id"),
            max_retries=int(d.get("max_retries", DEFAULT_MAX_RETRIES)),
            retry_count=int(d.get("retry_count", 0)),
            timeout_seconds=int(d.get("timeout_seconds", DEFAULT_TIMEOUT)),
            dispatched_at=d.get("dispatched_at"),
            completed_at=d.get("completed_at"),
            fail_reason=d.get("fail_reason"),
        )


# ---------------------------------------------------------------------------
# Task Registry
# ---------------------------------------------------------------------------

class TaskRegistry:
    """Manages the task list: load, save, query, update."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path
        self.tasks: dict[str, Task] = {}

    def load(self, path: Optional[Path] = None) -> None:
        p = path or self.path
        if not p or not p.exists():
            return
        self.path = p
        raw = _load_yaml_or_json(p)
        for item in raw:
            task = Task.from_dict(item)
            self.tasks[task.task_id] = task

    def save(self, path: Optional[Path] = None) -> None:
        p = path or self.path
        if not p:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"tasks": [t.to_dict() for t in self.tasks.values()]}
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(str(tmp), str(p))

    def add_task(self, goal: str, context: str = "",
                 dependencies: Optional[list[str]] = None,
                 task_id: Optional[str] = None,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 timeout_seconds: int = DEFAULT_TIMEOUT) -> Task:
        tid = task_id or str(uuid.uuid4())[:8]
        task = Task(
            task_id=tid,
            goal=goal,
            context=context,
            dependencies=dependencies or [],
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
        )
        self.tasks[tid] = task
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    def all_tasks(self) -> list[Task]:
        return list(self.tasks.values())

    def by_status(self, status: str) -> list[Task]:
        return [t for t in self.tasks.values() if t.status == status]

    def active_count(self) -> int:
        return len([t for t in self.tasks.values() if t.status in ACTIVE_STATUSES])

    def deps_met(self, task: Task) -> bool:
        """Check if all dependencies of a task are completed."""
        for dep_id in task.dependencies:
            dep = self.tasks.get(dep_id)
            if dep is None or dep.status != "completed":
                return False
        return True

    def deps_failed(self, task: Task) -> bool:
        """Check if any dependency has permanently failed."""
        for dep_id in task.dependencies:
            dep = self.tasks.get(dep_id)
            if dep is not None and dep.status == "failed":
                return True
        return False


# ---------------------------------------------------------------------------
# Transcript Discovery
# ---------------------------------------------------------------------------

def discover_transcripts(live_dir: Path = LIVE_DIR) -> dict[str, str]:
    """Return {deleg_id: log_path} for all live transcript files."""
    found: dict[str, str] = {}
    pattern = str(live_dir / TRANSCRIPT_GLOB)
    for p in glob.glob(pattern):
        parts = Path(p).parts
        for part in parts:
            if part.startswith("deleg_"):
                found[part] = p
                break
    return found


# ---------------------------------------------------------------------------
# Director Controller
# ---------------------------------------------------------------------------

class DirectorController:
    """Orchestrates task dispatch, monitoring, and retry logic."""

    def __init__(self, registry: TaskRegistry, *,
                 max_slots: int = MAX_SLOTS,
                 poll_interval: int = POLL_INTERVAL,
                 stuck_threshold: int = STUCK_THRESHOLD,
                 status_path: Path = STATUS_PATH,
                 log_path: Path = LOG_PATH,
                 dispatch_queue_path: Path = DISPATCH_QUEUE_PATH,
                 live_dir: Path = LIVE_DIR):
        self.registry = registry
        self.max_slots = max_slots
        self.poll_interval = poll_interval
        self.stuck_threshold = stuck_threshold
        self.status_path = status_path
        self.log_path = log_path
        self.dispatch_queue_path = dispatch_queue_path
        self.live_dir = live_dir
        self._running = True

    def _handle_signal(self, signum: int, frame: object) -> None:
        self._running = False

    # -- Dispatch Logic -----------------------------------------------------

    def dispatchable_tasks(self) -> list[Task]:
        """Return tasks that are pending with all deps met, ordered by task_id."""
        candidates = []
        for task in self.registry.by_status("pending"):
            if self.registry.deps_met(task):
                candidates.append(task)
        candidates.sort(key=lambda t: t.task_id)
        return candidates

    def dispatch_batch(self) -> list[Task]:
        """Dispatch up to (max_slots - active_count) tasks. Returns dispatched list."""
        active = self.registry.active_count()
        available = self.max_slots - active
        if available <= 0:
            return []

        candidates = self.dispatchable_tasks()
        batch = candidates[:available]

        for task in batch:
            task.status = "dispatched"
            task.dispatched_at = _now_iso()
            _log_event("DISPATCH", task.task_id,
                       f"goal={task.goal[:80]}", log_path=self.log_path)

        if batch:
            self._write_dispatch_queue(batch)
            self.registry.save()

        return batch

    def _write_dispatch_queue(self, tasks: list[Task]) -> None:
        """Write/append tasks to the dispatch queue for Hermes to consume."""
        queue: list[dict[str, Any]] = []
        # Read existing queue
        if self.dispatch_queue_path.exists():
            try:
                with open(self.dispatch_queue_path) as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        queue = data
                    elif isinstance(data, dict) and "tasks" in data:
                        queue = data["tasks"]
            except (json.JSONDecodeError, OSError):
                pass

        for task in tasks:
            queue.append({
                "task_id": task.task_id,
                "goal": task.goal,
                "context": task.context,
                "status": "ready_to_dispatch",
                "dispatched_at": task.dispatched_at,
            })

        self.dispatch_queue_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.dispatch_queue_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"tasks": queue, "updated_at": _now_iso()}, f, indent=2)
            f.write("\n")
        os.replace(str(tmp), str(self.dispatch_queue_path))

    # -- Monitor Logic ------------------------------------------------------

    def monitor_tick(self) -> None:
        """One monitoring cycle: check transcripts, detect stuck/done, cascade."""
        now = time.time()
        transcripts = discover_transcripts(self.live_dir)

        # Check each active task
        for task in self.registry.all_tasks():
            if task.status not in ACTIVE_STATUSES:
                continue

            # Try to find the transcript for this task's agent
            t_path = self._find_transcript(task, transcripts)
            if t_path:
                task.transcript_path = t_path

            if not task.transcript_path:
                # Dispatched but no transcript yet — check timeout
                if task.status == "dispatched" and task.dispatched_at:
                    dispatch_time = self._parse_iso(task.dispatched_at)
                    if dispatch_time and (now - dispatch_time) > task.timeout_seconds:
                        self._mark_failed(task, "dispatch_timeout",
                                          f"no transcript after {_elapsed_human(now - dispatch_time)}")
                continue

            # Read transcript
            last_line, lc = _read_last_line(task.transcript_path)

            # Detect progress
            if lc != task.last_line_count:
                task.last_line_count = lc
                task.last_change_ts = now
                if task.status == "dispatched":
                    task.status = "running"
                    _log_event("RUNNING", task.task_id,
                               f"lines={lc}", log_path=self.log_path)
                elif task.status == "stuck":
                    task.status = "running"
                    _log_event("UNSTUCK", task.task_id, log_path=self.log_path)

            # Terminal status
            terminal = _detect_terminal_status(last_line)
            if terminal == "completed":
                self._mark_completed(task)
                continue
            elif terminal == "failed":
                self._mark_failed(task, "agent_failed", last_line[:120])
                continue

            # Stuck detection
            if task.status == "running" and task.last_change_ts > 0:
                idle = now - task.last_change_ts
                if idle >= self.stuck_threshold:
                    self._handle_stuck(task, idle)

            # Timeout detection
            if task.dispatched_at:
                dispatch_time = self._parse_iso(task.dispatched_at)
                if dispatch_time and (now - dispatch_time) > task.timeout_seconds:
                    self._mark_failed(task, "timeout",
                                      f"exceeded {task.timeout_seconds}s")

        # Cascade: mark pending tasks with failed deps as failed
        for task in self.registry.by_status("pending"):
            if self.registry.deps_failed(task):
                self._mark_failed(task, "dep_failed",
                                  "dependency permanently failed")

        # Try dispatching newly eligible tasks
        self.dispatch_batch()

        # Write status
        self._write_status()
        self._print_table()

    def _find_transcript(self, task: Task, transcripts: dict[str, str]) -> Optional[str]:
        """Match a task to its transcript file."""
        if task.agent_id and task.agent_id in transcripts:
            return transcripts[task.agent_id]
        # Also check if any transcript mentions the task_id
        for did, path in transcripts.items():
            if task.task_id in did:
                task.agent_id = did
                return path
        return None

    def _mark_completed(self, task: Task) -> None:
        task.status = "completed"
        task.completed_at = _now_iso()
        _log_event("COMPLETED", task.task_id,
                   f"retries={task.retry_count}", log_path=self.log_path)

    def _mark_failed(self, task: Task, reason: str, detail: str = "") -> None:
        task.status = "failed"
        task.fail_reason = reason
        task.completed_at = _now_iso()
        _log_event("FAILED", task.task_id,
                   f"reason={reason} {detail}".strip(), log_path=self.log_path)

    def _handle_stuck(self, task: Task, idle_seconds: float) -> None:
        """Handle a stuck agent: retry or fail."""
        _log_event("STUCK", task.task_id,
                   f"idle {_elapsed_human(idle_seconds)}", log_path=self.log_path)

        if task.retry_count < task.max_retries:
            task.retry_count += 1
            task.status = "pending"
            task.agent_id = None
            task.dispatched_at = None
            task.transcript_path = None
            task.last_line_count = 0
            task.last_change_ts = 0.0
            _log_event("RETRY", task.task_id,
                       f"attempt {task.retry_count}/{task.max_retries}",
                       log_path=self.log_path)
        else:
            self._mark_failed(task, "stuck_exhausted",
                              f"retries exhausted after {task.max_retries} attempts")

    @staticmethod
    def _parse_iso(iso_str: str) -> Optional[float]:
        """Parse ISO timestamp to epoch seconds."""
        try:
            # Handle both Z and +00:00 suffixes
            s = iso_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            return dt.timestamp()
        except (ValueError, AttributeError):
            return None

    # -- Output -------------------------------------------------------------

    def _write_status(self) -> None:
        """Write director-status.json."""
        rows = []
        for task in self.registry.all_tasks():
            rows.append(task.to_dict())

        counts: dict[str, int] = {}
        for task in self.registry.all_tasks():
            counts[task.status] = counts.get(task.status, 0) + 1

        payload = {
            "ts": _now_iso(),
            "poll_interval": self.poll_interval,
            "stuck_threshold_s": self.stuck_threshold,
            "max_slots": self.max_slots,
            "summary": counts,
            "total": len(rows),
            "active": self.registry.active_count(),
            "tasks": rows,
        }

        try:
            self.status_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.status_path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
                f.write("\n")
            os.replace(str(tmp), str(self.status_path))
        except OSError as e:
            print(f"[director] write error: {e}", file=sys.stderr)

    def _print_table(self) -> None:
        """Print a compact status table to stdout."""
        tasks = self.registry.all_tasks()
        if not tasks:
            print("[director] no tasks")
            return

        # Header
        hdr = f"{'ID':<10} {'STATUS':<12} {'RETRIES':<8} {'AGENT':<16} {'GOAL':<50}"
        print(f"\n[director] {_now_iso()}  slots={self.registry.active_count()}/{self.max_slots}")
        print("-" * len(hdr))
        print(hdr)
        print("-" * len(hdr))

        for task in tasks:
            goal_short = task.goal[:48] + ".." if len(task.goal) > 50 else task.goal
            agent = task.agent_id or "-"
            if len(agent) > 14:
                agent = agent[:14] + ".."
            retries = f"{task.retry_count}/{task.max_retries}"
            print(f"{task.task_id:<10} {task.status:<12} {retries:<8} {agent:<16} {goal_short:<50}")
        print()

    # -- Run Loop -----------------------------------------------------------

    def run(self, once: bool = False) -> None:
        """Main loop."""
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        _log_event("DIRECTOR", "controller", "started", log_path=self.log_path)
        print(f"[director] task registry: {self.registry.path}")
        print(f"[director] status → {self.status_path}")
        print(f"[director] log    → {self.log_path}")
        print(f"[director] queue  → {self.dispatch_queue_path}")
        print(f"[director] slots  → {self.max_slots}")

        # Initial dispatch
        self.dispatch_batch()

        while self._running:
            try:
                self.monitor_tick()
                self.registry.save()
            except Exception as e:
                print(f"[director] tick error: {e}", file=sys.stderr)
                _log_event("ERROR", "tick", str(e), log_path=self.log_path)
            if once:
                break
            time.sleep(self.poll_interval)

        _log_event("DIRECTOR", "controller", "stopped", log_path=self.log_path)
        print("[director] stopped")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> None:
    """Run the director loop."""
    tasks_path = Path(args.tasks)
    if not tasks_path.exists():
        print(f"[director] error: tasks file not found: {tasks_path}",
              file=sys.stderr)
        sys.exit(1)

    registry = TaskRegistry(tasks_path)
    registry.load()

    if not registry.tasks:
        print("[director] warning: no tasks loaded", file=sys.stderr)

    kwargs: dict[str, Any] = {}
    if args.max_slots:
        kwargs["max_slots"] = int(args.max_slots)
    if args.status_path:
        kwargs["status_path"] = Path(args.status_path)
    if args.log_path:
        kwargs["log_path"] = Path(args.log_path)

    ctrl = DirectorController(registry, **kwargs)
    ctrl.run(once=args.once)


def cmd_status(args: argparse.Namespace) -> None:
    """Print status from the JSON file."""
    sp = Path(args.status_path) if args.status_path else STATUS_PATH
    if not sp.exists():
        print(f"[director] no status file at {sp}", file=sys.stderr)
        sys.exit(1)

    with open(sp) as f:
        data = json.load(f)

    print(json.dumps(data, indent=2))


def cmd_add(args: argparse.Namespace) -> None:
    """Add a task to the registry."""
    tasks_path = Path(args.tasks)
    registry = TaskRegistry(tasks_path)
    if tasks_path.exists():
        registry.load()

    deps = args.deps.split(",") if args.deps else []
    task = registry.add_task(
        goal=args.goal,
        context=args.context or "",
        dependencies=deps,
        task_id=args.id,
        max_retries=int(args.max_retries) if args.max_retries else DEFAULT_MAX_RETRIES,
        timeout_seconds=int(args.timeout) if args.timeout else DEFAULT_TIMEOUT,
    )
    registry.save()
    print(f"[director] added task {task.task_id}: {task.goal}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="director",
        description="Director Controller — task management for Hermes agents",
    )
    sub = parser.add_subparsers(dest="command")

    # run
    p_run = sub.add_parser("run", help="Run the director loop")
    p_run.add_argument("--tasks", required=True, help="Path to tasks YAML/JSON")
    p_run.add_argument("--once", action="store_true", help="Single tick then exit")
    p_run.add_argument("--max-slots", type=int, default=None)
    p_run.add_argument("--status-path", default=None)
    p_run.add_argument("--log-path", default=None)

    # status
    p_status = sub.add_parser("status", help="Show current status")
    p_status.add_argument("--status-path", default=None)

    # add
    p_add = sub.add_parser("add", help="Add a task to the registry")
    p_add.add_argument("--tasks", required=True, help="Path to tasks YAML/JSON")
    p_add.add_argument("--goal", required=True)
    p_add.add_argument("--context", default="")
    p_add.add_argument("--deps", default="", help="Comma-separated dependency task IDs")
    p_add.add_argument("--id", default=None, help="Explicit task ID")
    p_add.add_argument("--max-retries", default=None)
    p_add.add_argument("--timeout", default=None)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        cmd_run(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "add":
        cmd_add(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
