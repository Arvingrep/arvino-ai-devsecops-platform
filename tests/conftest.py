from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from arvino.clock import FakeClock
from arvino.db import init_schema, make_engine, make_session_factory
from arvino.domain import ArtifactSpec, Principal, PrincipalKind, Profile
from arvino.engine import Engine, EngineSettings
from arvino.fakes import (
    FakeGit,
    RecordingNotifier,
    RecordingTracker,
    RecordOnlyPromoter,
    ScriptedExecutor,
    StaticPolicy,
)
from arvino.ports import PolicyClient, Ports, StepOutcome, StepRequest
from arvino.views import ApprovalView, RunView
from arvino.workflows.model import load_registry

REPO = "acme/app"
SHA = "a" * 40
ROOT = Path(__file__).resolve().parent.parent
OWNER = Principal(id="owner", kind=PrincipalKind.HUMAN, roles=("owner",))
AGENT = Principal(id="agent:developer", kind=PrincipalKind.AGENT, roles=())
BASE_CHECKS = ["unit-tests", "lint", "secret-scan"]
ALL_CHECKS = [*BASE_CHECKS, "trivy", "conftest"]


@dataclass
class Env:
    engine: Engine
    executor: ScriptedExecutor
    git: FakeGit
    policy: StaticPolicy
    promoter: RecordOnlyPromoter
    tracker: RecordingTracker
    notifier: RecordingNotifier
    clock: FakeClock
    project_id: str
    issue_id: str
    db_url: str

    def start(self, profile: Profile, key: str = "k1") -> RunView:
        view, _ = self.engine.start_run(self.project_id, self.issue_id, profile, key, "owner")
        return view

    def pr_script(
        self, paths: list[str] | None = None, number: int = 1, repo: str = REPO
    ) -> Callable[[StepRequest], StepOutcome]:
        """Executor script for the pull_request step: registers a PR in the fake Git provider."""

        def _script(req: StepRequest) -> StepOutcome:
            self.git.register_pr(repo, number, req.run_id, paths or ["src/app.py"], SHA)
            return StepOutcome(
                status="succeeded",
                artifacts=[
                    ArtifactSpec(
                        kind="pull-request",
                        name="pr",
                        uri=f"https://git.example/{repo}/pulls/{number}",
                        metadata={"number": number},
                    )
                ],
            )

        return _script

    def webhook_pr(self, run_id: str, sha: str = SHA, number: int = 1) -> None:
        self.engine.update_git_state(run_id, pr_number=number, head_sha=sha, source="test")

    def checks(
        self, run_id: str, names: list[str], conclusion: str = "success", sha: str = SHA
    ) -> None:
        for n in names:
            self.engine.record_check(run_id, n, sha, conclusion, source="test")  # type: ignore[arg-type]

    def converge(
        self, app: str, revision: str = SHA, health: str = "Healthy", sync: str = "Synced"
    ) -> None:
        self.engine.record_deployment(app, revision, health, sync)

    def approve(self, run_id: str, step_key: str, principal: Principal = OWNER) -> ApprovalView:
        appr = next(a for a in self.engine.get_run(run_id).approvals if a.step_key == step_key)
        return self.engine.decide_approval(appr.id, principal, "approved")

    def complete(self, run_id: str, step_key: str, status: str = "succeeded", **kw: object) -> str:
        run = self.engine.get_run(run_id)
        st = run.step(step_key)
        out = StepOutcome(status=status, **kw)  # type: ignore[arg-type]
        if status == "succeeded" and not out.artifacts:
            out = out.model_copy(update={"artifacts": []})
        return self.engine.complete_step(run_id, step_key, st.attempt, out, AGENT).value


def build_env(
    tmp_path: Path | None = None,
    *,
    policy: PolicyClient | None = None,
    executor: ScriptedExecutor | None = None,
    agent_accounts: frozenset[str] = frozenset(),
) -> Env:
    url = f"sqlite:///{tmp_path / 'test.db'}" if tmp_path else "sqlite://"
    sa = make_engine(url)
    init_schema(sa)
    clock = FakeClock()
    executor = executor or ScriptedExecutor()
    git, promoter, tracker, notifier = (
        FakeGit(),
        RecordOnlyPromoter(),
        RecordingTracker(),
        RecordingNotifier(),
    )
    static = policy if policy is not None else StaticPolicy(allow=True)
    engine = Engine(
        make_session_factory(sa),
        load_registry(),
        Ports(
            executor=executor,
            tracker=tracker,
            git=git,
            promoter=promoter,
            policy=static,
            notifier=notifier,
        ),
        clock=clock,
        settings=EngineSettings(agent_accounts=agent_accounts),
    )
    project = engine.create_project(
        "acme",
        "Acme",
        repo_full_name=REPO,
        argocd_apps={
            "preview": "acme-preview",
            "staging": "acme-staging",
            "production": "acme-prod",
        },
    )
    issue, _ = engine.upsert_issue(project.id, "plane", "PLANE-1", "Add widget", body="please")
    return Env(
        engine=engine,
        executor=executor,
        git=git,
        policy=static if isinstance(static, StaticPolicy) else StaticPolicy(),
        promoter=promoter,
        tracker=tracker,
        notifier=notifier,
        clock=clock,
        project_id=project.id,
        issue_id=issue.id,
        db_url=url,
    )


@pytest.fixture
def env() -> Env:
    return build_env()


@pytest.fixture
def file_env(tmp_path: Path) -> Env:
    return build_env(tmp_path)
