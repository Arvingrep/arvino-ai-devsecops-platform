"""Tests for the Director Controller (scripts/director.py).

Covers: TaskRegistry, dependency resolution, dispatch logic, stuck detection,
retry behaviour, slot management, YAML/JSON parsing, CLI, and output files.
"""

from __future__ import annotations

import json
import sys
import textwrap
import time
from pathlib import Path

import pytest

# Ensure the scripts directory is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import director
from director import (
    DirectorController,
    Task,
    TaskRegistry,
    _detect_terminal_status,
    _elapsed_human,
    _now_iso,
    _parse_simple_yaml,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    """Provide a clean temp directory."""
    return tmp_path


@pytest.fixture
def sample_tasks() -> list[dict]:
    return [
        {"task_id": "t1", "goal": "Build API", "context": "REST endpoints",
         "dependencies": [], "status": "pending"},
        {"task_id": "t2", "goal": "Build UI", "context": "React frontend",
         "dependencies": ["t1"], "status": "pending"},
        {"task_id": "t3", "goal": "Write tests", "context": "pytest",
         "dependencies": ["t1"], "status": "pending"},
        {"task_id": "t4", "goal": "Deploy", "context": "K8s",
         "dependencies": ["t2", "t3"], "status": "pending"},
    ]


@pytest.fixture
def registry(sample_tasks: list[dict], tmp_dir: Path) -> TaskRegistry:
    """Create a pre-loaded registry."""
    p = tmp_dir / "tasks.json"
    p.write_text(json.dumps({"tasks": sample_tasks}, indent=2))
    reg = TaskRegistry(p)
    reg.load()
    return reg


@pytest.fixture
def controller(registry: TaskRegistry, tmp_dir: Path) -> DirectorController:
    return DirectorController(
        registry,
        max_slots=10,
        poll_interval=1,
        stuck_threshold=5,
        status_path=tmp_dir / "status.json",
        log_path=tmp_dir / "events.log",
        dispatch_queue_path=tmp_dir / "dispatch-queue.json",
        live_dir=tmp_dir / "live",
    )


# ---------------------------------------------------------------------------
# Task model tests
# ---------------------------------------------------------------------------

class TestTask:
    def test_from_dict_defaults(self):
        t = Task.from_dict({"task_id": "x", "goal": "test"})
        assert t.task_id == "x"
        assert t.goal == "test"
        assert t.status == "pending"
        assert t.dependencies == []
        assert t.retry_count == 0
        assert t.max_retries == 2

    def test_from_dict_with_id_fallback(self):
        t = Task.from_dict({"id": "y", "goal": "g"})
        assert t.task_id == "y"

    def test_to_dict_excludes_runtime_fields(self):
        t = Task(task_id="a", goal="g")
        t.last_line_count = 42
        t.last_change_ts = 999.0
        t.transcript_path = "/tmp/x"
        d = t.to_dict()
        assert "last_line_count" not in d
        assert "last_change_ts" not in d
        assert "transcript_path" not in d

    def test_roundtrip(self):
        original = Task(task_id="r", goal="roundtrip", context="ctx",
                        dependencies=["a", "b"], max_retries=5)
        d = original.to_dict()
        restored = Task.from_dict(d)
        assert restored.task_id == original.task_id
        assert restored.goal == original.goal
        assert restored.dependencies == original.dependencies
        assert restored.max_retries == original.max_retries


# ---------------------------------------------------------------------------
# YAML parser tests
# ---------------------------------------------------------------------------

class TestYAMLParser:
    def test_simple_list(self):
        text = textwrap.dedent("""\
        - task_id: t1
          goal: Build API
          dependencies: []
          status: pending
        - task_id: t2
          goal: Build UI
          dependencies: [t1]
        """)
        tasks = _parse_simple_yaml(text)
        assert len(tasks) == 2
        assert tasks[0]["task_id"] == "t1"
        assert tasks[0]["dependencies"] == []
        assert tasks[1]["dependencies"] == ["t1"]

    def test_top_level_key_ignored(self):
        text = textwrap.dedent("""\
        tasks:
        - task_id: t1
          goal: Do stuff
          max_retries: 3
        """)
        tasks = _parse_simple_yaml(text)
        assert len(tasks) == 1
        assert tasks[0]["max_retries"] == 3

    def test_quoted_values(self):
        text = textwrap.dedent("""\
        - task_id: "t1"
          goal: 'Build something'
        """)
        tasks = _parse_simple_yaml(text)
        assert tasks[0]["task_id"] == "t1"
        assert tasks[0]["goal"] == "Build something"

    def test_boolean_and_null(self):
        text = textwrap.dedent("""\
        - task_id: t1
          goal: test
          agent_id: null
        """)
        tasks = _parse_simple_yaml(text)
        assert tasks[0]["agent_id"] is None

    def test_comments_skipped(self):
        text = textwrap.dedent("""\
        # This is a comment
        - task_id: t1
          goal: test
          # inline comment
        """)
        tasks = _parse_simple_yaml(text)
        assert len(tasks) == 1


# ---------------------------------------------------------------------------
# TaskRegistry tests
# ---------------------------------------------------------------------------

class TestTaskRegistry:
    def test_load_json(self, tmp_dir: Path, sample_tasks: list[dict]):
        p = tmp_dir / "tasks.json"
        p.write_text(json.dumps({"tasks": sample_tasks}))
        reg = TaskRegistry(p)
        reg.load()
        assert len(reg.tasks) == 4
        assert reg.get("t1") is not None
        assert reg.get("t4") is not None

    def test_load_yaml(self, tmp_dir: Path):
        p = tmp_dir / "tasks.yaml"
        p.write_text(textwrap.dedent("""\
        - task_id: y1
          goal: YAML task
          status: pending
        """))
        reg = TaskRegistry(p)
        reg.load()
        assert len(reg.tasks) == 1
        assert reg.get("y1").goal == "YAML task"

    def test_save_and_reload(self, registry: TaskRegistry, tmp_dir: Path):
        out = tmp_dir / "out.json"
        registry.save(out)
        reg2 = TaskRegistry(out)
        reg2.load()
        assert len(reg2.tasks) == len(registry.tasks)
        for tid in registry.tasks:
            assert reg2.get(tid) is not None

    def test_add_task(self, tmp_dir: Path):
        reg = TaskRegistry(tmp_dir / "new.json")
        task = reg.add_task(goal="New task", context="ctx", task_id="n1")
        assert task.task_id == "n1"
        assert reg.get("n1") is task

    def test_by_status(self, registry: TaskRegistry):
        pending = registry.by_status("pending")
        assert len(pending) == 4

    def test_active_count(self, registry: TaskRegistry):
        assert registry.active_count() == 0
        registry.get("t1").status = "running"
        assert registry.active_count() == 1
        registry.get("t2").status = "dispatched"
        assert registry.active_count() == 2

    def test_deps_met_no_deps(self, registry: TaskRegistry):
        assert registry.deps_met(registry.get("t1")) is True

    def test_deps_met_pending(self, registry: TaskRegistry):
        assert registry.deps_met(registry.get("t2")) is False

    def test_deps_met_after_completion(self, registry: TaskRegistry):
        registry.get("t1").status = "completed"
        assert registry.deps_met(registry.get("t2")) is True
        assert registry.deps_met(registry.get("t3")) is True
        # t4 needs both t2 and t3
        assert registry.deps_met(registry.get("t4")) is False

    def test_deps_failed(self, registry: TaskRegistry):
        assert registry.deps_failed(registry.get("t2")) is False
        registry.get("t1").status = "failed"
        assert registry.deps_failed(registry.get("t2")) is True

    def test_load_nonexistent_is_noop(self, tmp_dir: Path):
        reg = TaskRegistry(tmp_dir / "nope.json")
        reg.load()  # should not raise
        assert len(reg.tasks) == 0

    def test_load_json_list_format(self, tmp_dir: Path):
        """JSON file that's a plain list (not wrapped in {tasks: ...})."""
        p = tmp_dir / "list.json"
        p.write_text(json.dumps([
            {"task_id": "a", "goal": "task a"},
            {"task_id": "b", "goal": "task b"},
        ]))
        reg = TaskRegistry(p)
        reg.load()
        assert len(reg.tasks) == 2


# ---------------------------------------------------------------------------
# DirectorController — dispatch tests
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_dispatch_root_tasks(self, controller: DirectorController):
        """Only t1 has no deps, so only t1 should dispatch."""
        batch = controller.dispatch_batch()
        assert len(batch) == 1
        assert batch[0].task_id == "t1"
        assert batch[0].status == "dispatched"

    def test_dispatch_respects_deps(self, controller: DirectorController):
        # Dispatch t1
        controller.dispatch_batch()
        # Complete t1
        controller.registry.get("t1").status = "completed"
        # Now t2 and t3 should be dispatchable
        batch = controller.dispatch_batch()
        ids = {t.task_id for t in batch}
        assert ids == {"t2", "t3"}

    def test_dispatch_cascades(self, controller: DirectorController):
        # Complete all deps for t4
        for tid in ["t1", "t2", "t3"]:
            controller.registry.get(tid).status = "completed"
        batch = controller.dispatch_batch()
        assert len(batch) == 1
        assert batch[0].task_id == "t4"

    def test_dispatch_respects_slot_limit(self, tmp_dir: Path):
        """With max_slots=2, only 2 tasks should dispatch."""
        reg = TaskRegistry()
        for i in range(5):
            reg.add_task(goal=f"task {i}", task_id=f"s{i}")

        ctrl = DirectorController(
            reg, max_slots=2,
            status_path=tmp_dir / "status.json",
            log_path=tmp_dir / "events.log",
            dispatch_queue_path=tmp_dir / "dq.json",
            live_dir=tmp_dir / "live",
        )
        batch = ctrl.dispatch_batch()
        assert len(batch) == 2
        assert reg.active_count() == 2

        # Next dispatch should return 0 (slots full)
        batch2 = ctrl.dispatch_batch()
        assert len(batch2) == 0

    def test_dispatch_queue_written(self, controller: DirectorController,
                                     tmp_dir: Path):
        controller.dispatch_batch()
        assert controller.dispatch_queue_path.exists()
        data = json.loads(controller.dispatch_queue_path.read_text())
        assert "tasks" in data
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["task_id"] == "t1"
        assert data["tasks"][0]["status"] == "ready_to_dispatch"

    def test_dispatch_queue_accumulates(self, controller: DirectorController):
        # First dispatch
        controller.dispatch_batch()
        # Complete t1 and dispatch again
        controller.registry.get("t1").status = "completed"
        controller.dispatch_batch()
        data = json.loads(controller.dispatch_queue_path.read_text())
        assert len(data["tasks"]) == 3  # t1 + t2 + t3


# ---------------------------------------------------------------------------
# DirectorController — monitoring tests
# ---------------------------------------------------------------------------

class TestMonitor:
    def _make_transcript(self, live_dir: Path, deleg_id: str,
                          content: str) -> Path:
        d = live_dir / deleg_id
        d.mkdir(parents=True, exist_ok=True)
        log = d / "task-main.log"
        log.write_text(content)
        return log

    def test_detects_completed(self, controller: DirectorController,
                                tmp_dir: Path):
        task = controller.registry.get("t1")
        task.status = "running"
        task.agent_id = "deleg_t1"
        task.dispatched_at = _now_iso()
        task.last_change_ts = time.time()

        live_dir = tmp_dir / "live"
        controller.live_dir = live_dir
        self._make_transcript(live_dir, "deleg_t1",
                               "working...\nstatus=completed\n")

        controller.monitor_tick()
        assert task.status == "completed"

    def test_detects_failed(self, controller: DirectorController,
                             tmp_dir: Path):
        task = controller.registry.get("t1")
        task.status = "running"
        task.agent_id = "deleg_t1"
        task.dispatched_at = _now_iso()
        task.last_change_ts = time.time()

        live_dir = tmp_dir / "live"
        controller.live_dir = live_dir
        self._make_transcript(live_dir, "deleg_t1",
                               "error!\nstatus=failed\n")

        controller.monitor_tick()
        assert task.status == "failed"
        assert task.fail_reason == "agent_failed"

    def test_detects_stuck_and_retries(self, controller: DirectorController,
                                        tmp_dir: Path):
        task = controller.registry.get("t1")
        task.status = "running"
        task.agent_id = "deleg_t1"
        task.dispatched_at = _now_iso()
        task.last_change_ts = time.time() - 10  # 10s ago, threshold is 5s
        task.last_line_count = 5

        live_dir = tmp_dir / "live"
        controller.live_dir = live_dir
        self._make_transcript(live_dir, "deleg_t1",
                               "line1\nline2\nline3\nline4\nline5\n")

        controller.monitor_tick()
        # Should retry since retry_count < max_retries
        assert task.status in ("pending", "dispatched")
        assert task.retry_count == 1

    def test_stuck_exhausted_fails(self, controller: DirectorController,
                                    tmp_dir: Path):
        task = controller.registry.get("t1")
        task.status = "running"
        task.agent_id = "deleg_t1"
        task.dispatched_at = _now_iso()
        task.last_change_ts = time.time() - 10
        task.last_line_count = 5
        task.retry_count = 2  # already at max
        task.max_retries = 2

        live_dir = tmp_dir / "live"
        controller.live_dir = live_dir
        self._make_transcript(live_dir, "deleg_t1",
                               "line1\nline2\nline3\nline4\nline5\n")

        controller.monitor_tick()
        assert task.status == "failed"
        assert task.fail_reason == "stuck_exhausted"

    def test_cascading_failure(self, controller: DirectorController,
                                tmp_dir: Path):
        """When t1 fails, downstream t2/t3 should also fail."""
        controller.registry.get("t1").status = "failed"
        controller.registry.get("t1").fail_reason = "agent_failed"

        live_dir = tmp_dir / "live"
        controller.live_dir = live_dir

        controller.monitor_tick()
        assert controller.registry.get("t2").status == "failed"
        assert controller.registry.get("t3").status == "failed"

    def test_status_file_written(self, controller: DirectorController,
                                  tmp_dir: Path):
        controller.live_dir = tmp_dir / "live"
        controller.monitor_tick()
        assert controller.status_path.exists()
        data = json.loads(controller.status_path.read_text())
        assert "tasks" in data
        assert "summary" in data
        assert data["max_slots"] == 10

    def test_event_log_written(self, controller: DirectorController,
                                tmp_dir: Path):
        controller.live_dir = tmp_dir / "live"
        controller.dispatch_batch()
        assert controller.log_path.exists()
        content = controller.log_path.read_text()
        assert "DISPATCH" in content

    def test_dispatched_transitions_to_running(self, controller: DirectorController,
                                                tmp_dir: Path):
        task = controller.registry.get("t1")
        task.status = "dispatched"
        task.agent_id = "deleg_t1"
        task.dispatched_at = _now_iso()

        live_dir = tmp_dir / "live"
        controller.live_dir = live_dir
        self._make_transcript(live_dir, "deleg_t1", "started\nworking\n")

        controller.monitor_tick()
        assert task.status == "running"


# ---------------------------------------------------------------------------
# Slot management edge cases
# ---------------------------------------------------------------------------

class TestSlotManagement:
    def test_slots_freed_on_completion(self, tmp_dir: Path):
        reg = TaskRegistry()
        for i in range(5):
            reg.add_task(goal=f"task {i}", task_id=f"q{i}")

        ctrl = DirectorController(
            reg, max_slots=3,
            status_path=tmp_dir / "status.json",
            log_path=tmp_dir / "events.log",
            dispatch_queue_path=tmp_dir / "dq.json",
            live_dir=tmp_dir / "live",
        )
        # Fill slots
        ctrl.dispatch_batch()
        assert reg.active_count() == 3

        # Complete one
        reg.get("q0").status = "completed"
        assert reg.active_count() == 2

        # Should dispatch one more
        batch = ctrl.dispatch_batch()
        assert len(batch) == 1
        assert reg.active_count() == 3

    def test_never_exceeds_max(self, tmp_dir: Path):
        reg = TaskRegistry()
        for i in range(20):
            reg.add_task(goal=f"task {i}", task_id=f"m{i}")

        ctrl = DirectorController(
            reg, max_slots=5,
            status_path=tmp_dir / "status.json",
            log_path=tmp_dir / "events.log",
            dispatch_queue_path=tmp_dir / "dq.json",
            live_dir=tmp_dir / "live",
        )
        ctrl.dispatch_batch()
        assert reg.active_count() <= 5


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_elapsed_human_seconds(self):
        assert _elapsed_human(45) == "0m45s"

    def test_elapsed_human_minutes(self):
        assert _elapsed_human(125) == "2m05s"

    def test_elapsed_human_hours(self):
        assert _elapsed_human(3661) == "1h01m"

    def test_detect_completed(self):
        assert _detect_terminal_status("exit_reason=completed") == "completed"
        assert _detect_terminal_status("status=completed done") == "completed"

    def test_detect_failed(self):
        assert _detect_terminal_status("status=failed err") == "failed"
        assert _detect_terminal_status("exit_reason=stopped") == "failed"

    def test_detect_nothing(self):
        assert _detect_terminal_status("just working...") is None

    def test_now_iso_format(self):
        ts = _now_iso()
        assert ts.endswith("Z")
        assert "T" in ts


# ---------------------------------------------------------------------------
# CLI parser tests
# ---------------------------------------------------------------------------

class TestCLI:
    def test_run_parser(self):
        parser = director.build_parser()
        args = parser.parse_args(["run", "--tasks", "tasks.yaml", "--once"])
        assert args.command == "run"
        assert args.tasks == "tasks.yaml"
        assert args.once is True

    def test_status_parser(self):
        parser = director.build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_add_parser(self):
        parser = director.build_parser()
        args = parser.parse_args([
            "add", "--tasks", "tasks.yaml",
            "--goal", "Build it", "--context", "ctx",
            "--deps", "t1,t2", "--id", "t99",
        ])
        assert args.command == "add"
        assert args.goal == "Build it"
        assert args.deps == "t1,t2"
        assert args.id == "t99"

    def test_cmd_add(self, tmp_dir: Path):
        tasks_path = tmp_dir / "tasks.json"
        parser = director.build_parser()
        args = parser.parse_args([
            "add", "--tasks", str(tasks_path),
            "--goal", "New task", "--id", "new1",
        ])
        director.cmd_add(args)
        assert tasks_path.exists()
        data = json.loads(tasks_path.read_text())
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["task_id"] == "new1"

    def test_cmd_status(self, tmp_dir: Path, capsys: pytest.CaptureFixture):
        sp = tmp_dir / "status.json"
        sp.write_text(json.dumps({"ts": "now", "tasks": []}))
        parser = director.build_parser()
        args = parser.parse_args(["status", "--status-path", str(sp)])
        director.cmd_status(args)
        out = capsys.readouterr().out
        assert "tasks" in out


# ---------------------------------------------------------------------------
# Integration: full tick cycle
# ---------------------------------------------------------------------------

class TestIntegration:
    def _make_transcript(self, live_dir: Path, deleg_id: str,
                          content: str) -> Path:
        d = live_dir / deleg_id
        d.mkdir(parents=True, exist_ok=True)
        log = d / "task-main.log"
        log.write_text(content)
        return log

    def test_full_lifecycle(self, tmp_dir: Path):
        """Run a full lifecycle: dispatch → running → completed → cascade."""
        reg = TaskRegistry(tmp_dir / "tasks.json")
        reg.add_task(goal="Step 1", task_id="s1")
        reg.add_task(goal="Step 2", task_id="s2", dependencies=["s1"])
        reg.save()

        live_dir = tmp_dir / "live"
        ctrl = DirectorController(
            reg, max_slots=10,
            stuck_threshold=5,
            status_path=tmp_dir / "status.json",
            log_path=tmp_dir / "events.log",
            dispatch_queue_path=tmp_dir / "dq.json",
            live_dir=live_dir,
        )

        # Tick 1: dispatch s1
        ctrl.monitor_tick()
        assert reg.get("s1").status == "dispatched"
        assert reg.get("s2").status == "pending"

        # Simulate agent starting
        reg.get("s1").agent_id = "deleg_s1"
        self._make_transcript(live_dir, "deleg_s1", "starting...\nworking\n")

        # Tick 2: s1 → running
        ctrl.monitor_tick()
        assert reg.get("s1").status == "running"

        # Simulate completion
        self._make_transcript(live_dir, "deleg_s1",
                               "starting...\nworking\nstatus=completed\n")

        # Tick 3: s1 → completed, s2 → dispatched
        ctrl.monitor_tick()
        assert reg.get("s1").status == "completed"
        assert reg.get("s2").status == "dispatched"

        # Verify output files
        assert (tmp_dir / "status.json").exists()
        assert (tmp_dir / "events.log").exists()
        assert (tmp_dir / "dq.json").exists()

        status = json.loads((tmp_dir / "status.json").read_text())
        assert status["total"] == 2

        events = (tmp_dir / "events.log").read_text()
        assert "COMPLETED" in events
        assert "DISPATCH" in events

    def test_run_once(self, tmp_dir: Path):
        """Test the run() method with once=True."""
        reg = TaskRegistry(tmp_dir / "tasks.json")
        reg.add_task(goal="Solo", task_id="solo")
        reg.save()

        ctrl = DirectorController(
            reg, max_slots=10,
            status_path=tmp_dir / "status.json",
            log_path=tmp_dir / "events.log",
            dispatch_queue_path=tmp_dir / "dq.json",
            live_dir=tmp_dir / "live",
        )
        ctrl.run(once=True)
        assert reg.get("solo").status == "dispatched"
