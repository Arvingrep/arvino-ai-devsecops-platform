"""Idle work-filling scheduler.

While the engine is waiting on a long-running step (CI gate, approval, deploy),
this scheduler looks at backlog issues and starts runs for them — mimicking the
director's 'fill the wait' behaviour.

Design decisions (set by orchestrator):
- Max 2 concurrent backlog runs per project (don't flood agents)
- Only starts runs for issues with label 'backlog' and state 'backlog'
- Uses Profile.FAST for backlog runs (fastest workflow)
- Idempotency key: f"backlog:{issue_id}" — prevents duplicate runs
- Never starts a backlog run if the project already has 2+ active backlog runs
"""

from __future__ import annotations

import logging

from arvino.domain import Profile
from arvino.engine import Engine
from arvino.views import IssueView, RunView

log = logging.getLogger(__name__)

MAX_BACKLOG_RUNS_PER_PROJECT = 2
BACKLOG_LABEL = "backlog"
BACKLOG_PROFILE = Profile.FAST


def _is_backlog_run(run: RunView) -> bool:
    """Check if a run was started by the idle scheduler."""
    return run.requested_by == "system:idle_scheduler"


class IdleScheduler:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def fill_idle(self) -> list[str]:
        """Start backlog runs for projects that have idle capacity. Returns started run IDs."""
        started: list[str] = []
        for project in self._engine.list_projects():
            started.extend(self._fill_project(project.id))
        return started

    def _fill_project(self, project_id: str) -> list[str]:
        """For one project: count active backlog runs and start more if under cap."""
        runs = self._engine.list_runs(project_id=project_id, active_only=True)
        active_backlog = sum(1 for r in runs if _is_backlog_run(r))
        slots = MAX_BACKLOG_RUNS_PER_PROJECT - active_backlog
        if slots <= 0:
            return []
        issues = self._backlog_issues(project_id)
        started = []
        for issue in issues[:slots]:
            try:
                run, created = self._engine.start_run(
                    project_id,
                    issue.id,
                    BACKLOG_PROFILE,
                    f"backlog:{issue.id}",
                    "system:idle_scheduler",
                )
                if created:
                    log.info(
                        "idle_scheduler started backlog run %s for issue %s",
                        run.id,
                        issue.id,
                    )
                    started.append(run.id)
            except Exception as exc:
                log.warning("idle_scheduler failed to start run for %s: %s", issue.id, exc)
        return started

    def _backlog_issues(self, project_id: str) -> list[IssueView]:
        """Return backlog issues that don't already have an active run."""
        runs = self._engine.list_runs(project_id=project_id, active_only=True)
        active_issue_ids = {r.issue_id for r in runs}
        issues = []
        for issue in self._engine.list_issues(project_id):
            if (
                issue.state == "backlog"
                and BACKLOG_LABEL in issue.labels
                and issue.id not in active_issue_ids
            ):
                issues.append(issue)
        return issues
