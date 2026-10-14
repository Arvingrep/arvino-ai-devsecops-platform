"""Deterministic in-process fakes for every port.

Used by the test-suite and by the explicit ``sandbox`` adapter mode (Compose
smoke test). They are never selected implicitly in production configuration.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from arvino.domain import ArtifactSpec, Principal, run_branch_prefix
from arvino.ports import (
    Decision,
    IssueRef,
    PromotionRequest,
    PromotionResult,
    PullRequestInfo,
    StepOutcome,
    StepRequest,
)

Script = StepOutcome | Callable[[StepRequest], StepOutcome]


class ScriptedExecutor:
    """Executor whose behaviour is scripted per step key; records every call."""

    def __init__(self, default: Script | None = None) -> None:
        self.calls: list[StepRequest] = []
        self._scripts: dict[str, list[Script]] = defaultdict(list)
        self._default = default
        self._lock = threading.Lock()

    def script(self, step_key: str, *outcomes: Script) -> None:
        """Queue outcomes for a step key; the last one repeats once the queue is drained."""
        self._scripts[step_key].extend(outcomes)

    def calls_for(self, step_key: str) -> list[StepRequest]:
        return [c for c in self.calls if c.step_key == step_key]

    def execute(self, request: StepRequest) -> StepOutcome:
        with self._lock:
            self.calls.append(request)
            queue = self._scripts.get(request.step_key)
            item: Script | None
            item = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else self._default
        if item is None:
            return self.default_success(request)
        return item(request) if callable(item) else item

    @staticmethod
    def default_success(request: StepRequest) -> StepOutcome:
        return StepOutcome(
            status="succeeded",
            artifacts=[
                ArtifactSpec(
                    kind="note",
                    name=f"{request.step_key}-result",
                    uri=f"git://fake/{request.run_id}/{request.step_key}",
                    metadata={"attempt": request.attempt},
                )
            ],
        )


class QueueExecutor:
    """Pull-model executor: dispatch leaves the step `running`; an external agent
    harness (OpenHands / Claude Code wrapper) fetches work and posts completion."""

    def execute(self, request: StepRequest) -> StepOutcome:
        return StepOutcome(status="running")


class SandboxExecutor(ScriptedExecutor):
    """Sandbox-mode executor: succeeds every step; opens a PR in the in-memory FakeGit for
    `pull_request` steps so the whole pipeline can be exercised end to end without any
    external system. The changed paths come from ``ARVINO_SANDBOX_CHANGED_PATHS`` (comma
    separated) so smoke tests can also exercise the protected-path denial."""

    def __init__(self, git: FakeGit) -> None:
        super().__init__()
        self._git = git

    def execute(self, request: StepRequest) -> StepOutcome:
        if request.step_key != "pull_request" or request.repo is None:
            return super().execute(request)
        import hashlib
        import os

        paths = [
            p for p in os.environ.get("ARVINO_SANDBOX_CHANGED_PATHS", "src/app.py").split(",") if p
        ]
        sha = hashlib.sha1(request.run_id.encode(), usedforsecurity=False).hexdigest()
        number = int(sha[:6], 16)
        self._git.register_pr(request.repo, number, request.run_id, paths, sha)
        return StepOutcome(
            status="succeeded",
            artifacts=[
                ArtifactSpec(
                    kind="pull-request",
                    name="pr",
                    uri=f"sandbox://{request.repo}/pulls/{number}",
                    metadata={"number": number},
                )
            ],
        )


class RecordingTracker:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def post_status(self, issue: IssueRef, text: str) -> None:
        self.messages.append((issue.id, text))


class FakeGit:
    def __init__(self) -> None:
        self.prs: dict[tuple[str, int], PullRequestInfo] = {}
        self.paths: dict[tuple[str, int], list[str]] = {}
        self.pushed: list[tuple[str, str, str]] = []
        self.fail_changed_paths = False
        self._next = 1

    def push_branch(self, repo: str, branch: str, principal: Principal) -> None:
        self.pushed.append((repo, branch, principal.id))

    def open_pull_request(
        self, repo: str, head: str, base: str, title: str, body: str, principal: Principal
    ) -> PullRequestInfo:
        n, self._next = self._next, self._next + 1
        pr = PullRequestInfo(number=n, head_ref=head, head_sha=f"sha{n:040d}"[-40:], base_ref=base)
        self.prs[(repo, n)] = pr
        self.paths.setdefault((repo, n), [])
        return pr

    def get_pull_request(self, repo: str, number: int) -> PullRequestInfo:
        return self.prs[(repo, number)]

    def changed_paths(self, repo: str, number: int) -> list[str]:
        if self.fail_changed_paths:
            raise ConnectionError("git provider unavailable")
        return list(self.paths[(repo, number)])

    # test helper
    def register_pr(
        self, repo: str, number: int, run_id: str, paths: list[str], head_sha: str = "a" * 40
    ) -> PullRequestInfo:
        pr = PullRequestInfo(
            number=number,
            head_ref=f"{run_branch_prefix(run_id)}pull_request",
            head_sha=head_sha,
            base_ref="main",
        )
        self.prs[(repo, number)] = pr
        self.paths[(repo, number)] = paths
        return pr


class UnconfiguredGit:
    """Fails closed: the gate cannot pass without a real Git provider."""

    def _fail(self) -> Any:
        raise RuntimeError("no GitProvider adapter configured")

    def push_branch(self, repo: str, branch: str, principal: Principal) -> None:
        self._fail()

    def open_pull_request(self, *a: Any, **k: Any) -> PullRequestInfo:
        return self._fail()  # type: ignore[no-any-return]

    def get_pull_request(self, repo: str, number: int) -> PullRequestInfo:
        return self._fail()  # type: ignore[no-any-return]

    def changed_paths(self, repo: str, number: int) -> list[str]:
        return self._fail()  # type: ignore[no-any-return]


class RecordOnlyPromoter:
    """Records the promotion request; a human/GitOps commit performs the change."""

    def __init__(self) -> None:
        self.requests: list[PromotionRequest] = []

    def request_promotion(self, request: PromotionRequest) -> PromotionResult:
        self.requests.append(request)
        return PromotionResult(
            reference=f"gitops-promotion-requested:{request.environment}:{request.run_id}",
            detail={"revision": request.revision, "app": request.app},
        )


class StaticPolicy:
    def __init__(self, allow: bool = True, reasons: list[str] | None = None) -> None:
        self.allow = allow
        self.reasons = reasons or ([] if allow else ["denied by StaticPolicy"])
        self.inputs: list[dict[str, Any]] = []

    def decide(self, policy_input: dict[str, Any]) -> Decision:
        self.inputs.append(policy_input)
        return Decision(allow=self.allow, reasons=list(self.reasons))

    def actions(self) -> list[str]:
        return [str(i.get("action")) for i in self.inputs]


class RecordingNotifier:
    def __init__(self) -> None:
        self.pokes: list[str] = []

    def poke(self, run_id: str) -> None:
        self.pokes.append(run_id)
