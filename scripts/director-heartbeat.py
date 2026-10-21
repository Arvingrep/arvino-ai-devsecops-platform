#!/usr/bin/env python3
# 遗留（Hermes 时代）：每 15 秒扫 Hermes 委派记录，产出 director-status.json。
# Hermes 三个 cron job 已于 2026-10-21 暂停，本脚本不在接管链路内，保留仅供追溯；
# 接管链路里的心跳是 scripts/t1_heartbeat.py（租约感知，跑在集群 CronJob 里）。
"""Director Heartbeat Monitor

Watches Hermes delegation transcript files every 15 seconds and produces:
  - A compact status JSON   → ~/.hermes/cache/scratch/director-status.json
  - An append-only event log → ~/.hermes/cache/scratch/director-events.log

Pure stdlib — no external dependencies.

Usage:
    python3 scripts/director-heartbeat.py          # foreground
    python3 scripts/director-heartbeat.py &         # background
    python3 scripts/director-heartbeat.py --once    # single pass then exit
"""

from __future__ import annotations

import glob
import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
POLL_INTERVAL = 15  # seconds
STUCK_THRESHOLD = 180  # 3 minutes with no new lines → stuck
LIVE_DIR = Path.home() / ".hermes" / "cache" / "delegation" / "live"
STATUS_PATH = Path.home() / ".hermes" / "cache" / "scratch" / "director-status.json"
LOG_PATH = Path.home() / ".hermes" / "cache" / "scratch" / "director-events.log"
TRANSCRIPT_GLOB = "deleg_*/task-*.log"

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class AgentSnapshot:
    """Mutable state for a single delegation agent."""

    deleg_id: str
    log_path: str
    last_line: str = ""
    last_line_count: int = 0
    last_change_ts: float = field(default_factory=time.time)
    first_seen_ts: float = field(default_factory=time.time)
    status: str = "running"  # running | stuck | completed | stopped


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
            # Seek to end, walk back to find last newline
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return ("", 0)
            # Count lines fast
            f.seek(0)
            line_count = sum(1 for _ in f)
            # Get last non-empty line
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


def _detect_terminal_status(last_line: str) -> str | None:
    """If the transcript shows a final status, return it."""
    ll = last_line.lower()
    if "status=completed" in ll or "exit_reason=completed" in ll:
        return "completed"
    if "status=stopped" in ll or "exit_reason=stopped" in ll:
        return "stopped"
    if "status=failed" in ll or "exit_reason=failed" in ll:
        return "stopped"
    return None


def _log_event(event: str, deleg_id: str, detail: str = "") -> None:
    """Append a single event line to the log file."""
    ts = _now_iso()
    line = f"{ts}  {event:<12}  {deleg_id}"
    if detail:
        line += f"  {detail}"
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Core monitor
# ---------------------------------------------------------------------------


class HeartbeatMonitor:
    """Tracks delegation agents and writes status/events."""

    def __init__(self) -> None:
        self.agents: dict[str, AgentSnapshot] = {}
        self._running = True
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum: int, frame: object) -> None:
        self._running = False

    # -- discovery ----------------------------------------------------------

    def _discover(self) -> dict[str, str]:
        """Return {deleg_id: log_path} for all live transcript files."""
        found: dict[str, str] = {}
        pattern = str(LIVE_DIR / TRANSCRIPT_GLOB)
        for p in glob.glob(pattern):
            parts = Path(p).parts
            # Extract deleg_XXXX from path
            for part in parts:
                if part.startswith("deleg_"):
                    found[part] = p
                    break
        return found

    # -- tick ---------------------------------------------------------------

    def tick(self) -> None:
        """One monitoring pass."""
        now = time.time()
        live = self._discover()

        # Detect new agents
        for did, lpath in live.items():
            if did not in self.agents:
                self.agents[did] = AgentSnapshot(
                    deleg_id=did,
                    log_path=lpath,
                    first_seen_ts=now,
                    last_change_ts=now,
                )
                _log_event("START", did, lpath)

        # Update each tracked agent
        for did, snap in list(self.agents.items()):
            last_line, lc = _read_last_line(snap.log_path)

            # Check for transcript growth
            if lc != snap.last_line_count or last_line != snap.last_line:
                snap.last_line = last_line
                snap.last_line_count = lc
                snap.last_change_ts = now
                if snap.status == "stuck":
                    snap.status = "running"
                    _log_event("UNSTUCK", did)

            # Terminal status from transcript content
            terminal = _detect_terminal_status(last_line)
            if terminal and snap.status not in ("completed", "stopped"):
                snap.status = terminal
                _log_event(
                    terminal.upper(), did, f"after {_elapsed_human(now - snap.first_seen_ts)}"
                )
                continue

            # Stuck detection (only for running agents)
            if snap.status == "running":
                idle = now - snap.last_change_ts
                if idle >= STUCK_THRESHOLD:
                    snap.status = "stuck"
                    _log_event("STUCK", did, f"idle {_elapsed_human(idle)}")

        # Detect disappeared agents (dir removed but we tracked them)
        for did in list(self.agents):
            if did not in live and self.agents[did].status == "running":
                self.agents[did].status = "stopped"
                _log_event("VANISHED", did, "transcript dir removed")

        self._write_status(now)

    # -- output -------------------------------------------------------------

    def _write_status(self, now: float) -> None:
        """Write compact status JSON."""
        rows = []
        for _did, snap in sorted(self.agents.items()):
            age = now - snap.first_seen_ts
            idle = now - snap.last_change_ts
            # Truncate last line for display
            preview = snap.last_line[:120] + ("…" if len(snap.last_line) > 120 else "")
            rows.append(
                {
                    "id": snap.deleg_id,
                    "status": snap.status,
                    "age": _elapsed_human(age),
                    "idle": _elapsed_human(idle),
                    "lines": snap.last_line_count,
                    "preview": preview,
                }
            )

        counts = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1

        payload = {
            "ts": _now_iso(),
            "poll_interval": POLL_INTERVAL,
            "stuck_threshold_s": STUCK_THRESHOLD,
            "summary": counts,
            "total": len(rows),
            "agents": rows,
        }

        try:
            STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATUS_PATH.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(payload, f, indent=2)
                f.write("\n")
            os.replace(str(tmp), str(STATUS_PATH))
        except OSError as e:
            print(f"[heartbeat] write error: {e}", file=sys.stderr)

    # -- run loop -----------------------------------------------------------

    def run(self, once: bool = False) -> None:
        """Main loop. If once=True, do a single tick and exit."""
        _log_event("MONITOR", "heartbeat", "started")
        print(f"[heartbeat] monitoring {LIVE_DIR}")
        print(f"[heartbeat] status → {STATUS_PATH}")
        print(f"[heartbeat] log    → {LOG_PATH}")

        while self._running:
            try:
                self.tick()
            except Exception as e:
                print(f"[heartbeat] tick error: {e}", file=sys.stderr)
            if once:
                break
            time.sleep(POLL_INTERVAL)

        _log_event("MONITOR", "heartbeat", "stopped")
        print("[heartbeat] stopped")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> None:
    once = "--once" in sys.argv
    monitor = HeartbeatMonitor()
    monitor.run(once=once)


if __name__ == "__main__":
    main()
