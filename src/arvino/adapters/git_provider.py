"""Real GitProvider adapters for GitHub (gh CLI) and Gitea (httpx REST).

Both implement the four-method :class:`~arvino.ports.GitProvider` protocol.
There is deliberately **no merge method** — this is a design invariant.

GitHub adapter shells out to ``gh api`` so it inherits the user's auth token
without managing credentials directly.

Gitea adapter uses :mod:`httpx` with token-based auth against the REST v1 API.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import httpx

from arvino.domain import Principal
from arvino.ports import PullRequestInfo

# ---------------------------------------------------------------------------
# GitHub  (gh CLI)
# ---------------------------------------------------------------------------


class GitHubProvider:
    """GitProvider backed by the ``gh`` CLI.

    Requires ``gh`` to be installed and authenticated (``gh auth status``).
    ``push_branch`` delegates to ``git push`` via ``gh`` so the remote is
    resolved from the repo slug.
    """

    def push_branch(self, repo: str, branch: str, principal: Principal) -> None:
        self._gh(
            "api",
            f"/repos/{repo}/git/refs",
            "-X",
            "GET",
            "--jq",
            f'[.[] | select(.ref == "refs/heads/{branch}")] | length',
        )
        # We actually need to ensure the ref exists on the remote.  The engine
        # assumes a local commit was already made; push_branch is "make sure
        # the remote has it".  We create the ref via the API if it doesn't exist
        # or let git push handle it.  Since this adapter is for the control plane
        # (not the coding agent), we just push the current HEAD of that branch.
        self._run(
            ["git", "push", f"https://github.com/{repo}.git", f"{branch}:{branch}", "--force"],
        )

    def open_pull_request(
        self,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        principal: Principal,
    ) -> PullRequestInfo:
        raw = self._gh(
            "api",
            f"/repos/{repo}/pulls",
            "-X",
            "POST",
            "-f",
            f"head={head}",
            "-f",
            f"base={base}",
            "-f",
            f"title={title}",
            "-f",
            f"body={body}",
        )
        data = json.loads(raw)
        return PullRequestInfo(
            number=data["number"],
            head_ref=data["head"]["ref"],
            head_sha=data["head"]["sha"],
            base_ref=data["base"]["ref"],
            merged=data.get("merged", False),
        )

    def get_pull_request(self, repo: str, number: int) -> PullRequestInfo:
        raw = self._gh("api", f"/repos/{repo}/pulls/{number}")
        data = json.loads(raw)
        return PullRequestInfo(
            number=data["number"],
            head_ref=data["head"]["ref"],
            head_sha=data["head"]["sha"],
            base_ref=data["base"]["ref"],
            merged=data.get("merged", False),
        )

    def changed_paths(self, repo: str, number: int) -> list[str]:
        page = 1
        paths: list[str] = []
        while True:
            raw = self._gh(
                "api",
                f"/repos/{repo}/pulls/{number}/files?per_page=100&page={page}",
            )
            files: list[dict[str, Any]] = json.loads(raw)
            if not files:
                break
            paths.extend(f["filename"] for f in files)
            if len(files) < 100:
                break
            page += 1
        return paths

    # -- helpers --------------------------------------------------------

    @staticmethod
    def _gh(*args: str) -> str:
        result = subprocess.run(  # noqa: S603
            ["gh", *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"gh failed (rc={result.returncode}): {result.stderr.strip()}")
        return result.stdout

    @staticmethod
    def _run(cmd: list[str]) -> str:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603
        if result.returncode != 0:
            raise RuntimeError(f"{cmd[0]} failed (rc={result.returncode}): {result.stderr.strip()}")
        return result.stdout


# ---------------------------------------------------------------------------
# Gitea  (httpx REST v1)
# ---------------------------------------------------------------------------


class GiteaProvider:
    """GitProvider backed by the Gitea REST API v1 using :mod:`httpx`.

    Parameters
    ----------
    base_url:
        Gitea instance URL, e.g. ``https://gitea.example.com``.
    token:
        Personal-access or application token with ``repo`` scope.
    """

    def __init__(self, base_url: str, token: str) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/api/v1",
            headers={"Authorization": f"token {token}"},
            timeout=30.0,
        )

    def push_branch(self, repo: str, branch: str, principal: Principal) -> None:
        # Resolve the default branch's HEAD to create a ref.
        # In real usage the coding agent already pushed the commit; this
        # creates the branch ref from the repo's current default-branch HEAD
        # so the control plane can reference it.
        repo_resp = self._request("GET", f"/repos/{repo}")
        default_branch = repo_resp["default_branch"]
        branch_resp = self._request("GET", f"/repos/{repo}/branches/{default_branch}")
        _commit_sha = branch_resp["commit"]["id"]
        # Create the branch — ignore 409 (already exists).
        resp = self._client.post(
            f"/repos/{repo}/branches",
            json={"new_branch_name": branch, "old_branch_name": default_branch},
        )
        if resp.status_code == 409:
            return  # branch already exists
        resp.raise_for_status()

    def open_pull_request(
        self,
        repo: str,
        head: str,
        base: str,
        title: str,
        body: str,
        principal: Principal,
    ) -> PullRequestInfo:
        data = self._request(
            "POST",
            f"/repos/{repo}/pulls",
            json={"head": head, "base": base, "title": title, "body": body},
        )
        return self._to_pr_info(data)

    def get_pull_request(self, repo: str, number: int) -> PullRequestInfo:
        data = self._request("GET", f"/repos/{repo}/pulls/{number}")
        return self._to_pr_info(data)

    def changed_paths(self, repo: str, number: int) -> list[str]:
        page = 1
        paths: list[str] = []
        while True:
            resp = self._client.get(
                f"/repos/{repo}/pulls/{number}/files",
                params={"limit": 50, "page": page},
            )
            resp.raise_for_status()
            files: list[dict[str, Any]] = resp.json()
            if not files:
                break
            paths.extend(f["filename"] for f in files)
            if len(files) < 50:
                break
            page += 1
        return paths

    def close(self) -> None:
        self._client.close()

    # -- helpers --------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        resp = self._client.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    @staticmethod
    def _to_pr_info(data: dict[str, Any]) -> PullRequestInfo:
        return PullRequestInfo(
            number=data["number"],
            head_ref=data["head"]["ref"]
            if isinstance(data["head"], dict)
            else data["head"]["label"],
            head_sha=data["head"]["sha"] if isinstance(data["head"], dict) else "",
            base_ref=data["base"]["ref"]
            if isinstance(data["base"], dict)
            else data["base"]["label"],
            merged=data.get("merged", False),
        )
