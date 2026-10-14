"""Policy-enforcing wrapper for GitProvider adapters.

The :class:`~arvino.ports.GitProvider` port has no merge operation. This wrapper
additionally asks policy (`git.write`) before any branch write, so an agent principal
can only write ``arvino/*`` branches and never a protected one. It is a second layer:
the primary control is Git-server branch protection (see README, *Recovery & hardening*).
"""

from __future__ import annotations

from arvino.domain import PolicyDeniedError, Principal
from arvino.ports import GitProvider, PolicyClient, PullRequestInfo


class GuardedGitProvider:
    def __init__(self, inner: GitProvider, policy: PolicyClient) -> None:
        self._inner = inner
        self._policy = policy

    def _check(self, principal: Principal, operation: str, branch: str) -> None:
        decision = self._policy.decide(
            {
                "action": "git.write",
                "principal": principal.as_policy_input(),
                "git": {"operation": operation, "branch": branch},
            }
        )
        if not decision.allow:
            raise PolicyDeniedError(decision.reasons)

    def push_branch(self, repo: str, branch: str, principal: Principal) -> None:
        self._check(principal, "push", branch)
        self._inner.push_branch(repo, branch, principal)

    def open_pull_request(
        self, repo: str, head: str, base: str, title: str, body: str, principal: Principal
    ) -> PullRequestInfo:
        self._check(
            principal, "push", head
        )  # the PR source branch must be writable by the principal
        return self._inner.open_pull_request(repo, head, base, title, body, principal)

    def get_pull_request(self, repo: str, number: int) -> PullRequestInfo:
        return self._inner.get_pull_request(repo, number)

    def changed_paths(self, repo: str, number: int) -> list[str]:
        return self._inner.changed_paths(repo, number)
