"""Tests for the real GitProvider adapters (GitHub + Gitea).

All tests run offline — ``gh`` calls are monkeypatched and Gitea HTTP is
handled by ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from arvino.adapters.git_provider import GiteaProvider, GitHubProvider
from arvino.domain import Principal, PrincipalKind
from arvino.ports import PullRequestInfo

PRINCIPAL = Principal(id="agent:dev", kind=PrincipalKind.AGENT, roles=())
REPO = "acme/app"


# ---------------------------------------------------------------------------
# Structural: no merge anywhere
# ---------------------------------------------------------------------------


def test_github_provider_has_no_merge_method() -> None:
    public = {n for n in dir(GitHubProvider) if not n.startswith("_")}
    assert not any("merge" in n for n in public)


def test_gitea_provider_has_no_merge_method() -> None:
    public = {n for n in dir(GiteaProvider) if not n.startswith("_")}
    assert not any("merge" in n for n in public), f"found merge-like: {public}"


def test_both_satisfy_git_provider_protocol() -> None:
    """Both classes expose the four protocol methods expected by GitProvider."""
    required = {"push_branch", "open_pull_request", "get_pull_request", "changed_paths"}
    for cls in (GitHubProvider, GiteaProvider):
        public = {n for n in dir(cls) if not n.startswith("_")}
        assert required <= public, f"{cls.__name__} missing: {required - public}"


# ---------------------------------------------------------------------------
# GitHub adapter (monkeypatched subprocess)
# ---------------------------------------------------------------------------


def _make_gh_runner(responses: dict[str, str | list[str]]):
    """Return a function that replaces GitHubProvider._gh / _run.

    ``responses`` maps a substring of the joined arg string to a JSON response
    (or list of responses consumed in order).
    """
    consumed: dict[str, int] = {}

    def fake_gh(*args: str) -> str:
        key_str = " ".join(args)
        for pattern, resp in responses.items():
            if pattern in key_str:
                if isinstance(resp, list):
                    idx = consumed.get(pattern, 0)
                    consumed[pattern] = idx + 1
                    return resp[idx] if idx < len(resp) else resp[-1]
                return resp
        raise AssertionError(f"unmatched gh call: {key_str}")

    return fake_gh


class TestGitHubPushBranch:
    def test_push_branch_calls_git_push(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str]) -> str:
            calls.append(cmd)
            return ""

        gh = GitHubProvider()
        with patch.object(gh, "_gh", return_value="0"), patch.object(gh, "_run", fake_run):
            gh.push_branch(REPO, "arvino/run-1/x", PRINCIPAL)

        assert len(calls) == 1
        assert "git" in calls[0][0]
        assert f"https://github.com/{REPO}.git" in calls[0]
        assert "arvino/run-1/x:arvino/run-1/x" in calls[0]


class TestGitHubOpenPR:
    PR_RESPONSE = json.dumps(
        {
            "number": 42,
            "head": {"ref": "arvino/run-1/pr", "sha": "abc123" + "0" * 34},
            "base": {"ref": "main"},
            "merged": False,
        }
    )

    def test_returns_pull_request_info(self) -> None:
        gh = GitHubProvider()
        runner = _make_gh_runner({"/pulls": self.PR_RESPONSE})
        with patch.object(gh, "_gh", runner):
            pr = gh.open_pull_request(REPO, "arvino/run-1/pr", "main", "Title", "Body", PRINCIPAL)

        assert isinstance(pr, PullRequestInfo)
        assert pr.number == 42
        assert pr.head_ref == "arvino/run-1/pr"
        assert pr.base_ref == "main"
        assert pr.merged is False


class TestGitHubGetPR:
    PR_RESPONSE = json.dumps(
        {
            "number": 7,
            "head": {"ref": "feature/x", "sha": "def456" + "0" * 34},
            "base": {"ref": "develop"},
            "merged": True,
        }
    )

    def test_returns_pull_request_info(self) -> None:
        gh = GitHubProvider()
        runner = _make_gh_runner({"/pulls/7": self.PR_RESPONSE})
        with patch.object(gh, "_gh", runner):
            pr = gh.get_pull_request(REPO, 7)

        assert pr.number == 7
        assert pr.merged is True
        assert pr.head_ref == "feature/x"


class TestGitHubChangedPaths:
    FILES_P1 = json.dumps([{"filename": "src/a.py"}, {"filename": "src/b.py"}])
    FILES_EMPTY = json.dumps([])

    def test_single_page(self) -> None:
        gh = GitHubProvider()
        runner = _make_gh_runner({"/files": [self.FILES_P1, self.FILES_EMPTY]})
        with patch.object(gh, "_gh", runner):
            paths = gh.changed_paths(REPO, 10)

        assert paths == ["src/a.py", "src/b.py"]

    def test_pagination(self) -> None:
        page1 = json.dumps([{"filename": f"f{i}.py"} for i in range(100)])
        page2 = json.dumps([{"filename": "last.py"}])
        gh = GitHubProvider()
        runner = _make_gh_runner({"/files": [page1, page2]})
        with patch.object(gh, "_gh", runner):
            paths = gh.changed_paths(REPO, 1)

        assert len(paths) == 101
        assert paths[-1] == "last.py"


class TestGitHubErrors:
    def test_gh_failure_raises_runtime_error(self) -> None:
        gh = GitHubProvider()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "auth required"
            mock_run.return_value.stdout = ""
            with pytest.raises(RuntimeError, match="gh failed"):
                gh.get_pull_request(REPO, 1)


# ---------------------------------------------------------------------------
# Gitea adapter (httpx.MockTransport)
# ---------------------------------------------------------------------------


def _gitea_transport(routes: dict[tuple[str, str], Any]) -> httpx.MockTransport:
    """Build a mock transport from {(METHOD, path_suffix): response_body_or_callable}."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Use path without query string for matching
        path = request.url.path
        for (method, suffix), body in routes.items():
            if request.method == method and path.endswith(suffix):
                if callable(body):
                    return body(request)
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"message": f"no route: {request.method} {path}"})

    return httpx.MockTransport(handler)


def _make_gitea(routes: dict[tuple[str, str], Any]) -> GiteaProvider:
    transport = _gitea_transport(routes)
    provider = GiteaProvider.__new__(GiteaProvider)
    provider._client = httpx.Client(
        base_url="https://gitea.test/api/v1",
        transport=transport,
        headers={"Authorization": "token fake"},
    )
    return provider


_PR_DATA: dict[str, Any] = {
    "number": 5,
    "head": {"ref": "arvino/r1/pr", "sha": "aaa" + "0" * 37},
    "base": {"ref": "main"},
    "merged": False,
}


class TestGiteaPushBranch:
    def test_creates_branch_via_api(self) -> None:
        requests_log: list[httpx.Request] = []

        def branch_create(req: httpx.Request) -> httpx.Response:
            requests_log.append(req)
            return httpx.Response(201, json={"name": "new-branch"})

        provider = _make_gitea(
            {
                ("GET", f"/repos/{REPO}"): {"default_branch": "main"},
                ("GET", f"/repos/{REPO}/branches/main"): {"commit": {"id": "abc" * 13 + "a"}},
                ("POST", f"/repos/{REPO}/branches"): branch_create,
            }
        )

        provider.push_branch(REPO, "arvino/run-1/dev", PRINCIPAL)
        assert len(requests_log) == 1
        body = json.loads(requests_log[0].content)
        assert body["new_branch_name"] == "arvino/run-1/dev"

    def test_ignores_409_branch_exists(self) -> None:
        def branch_conflict(req: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"message": "already exists"})

        provider = _make_gitea(
            {
                ("GET", f"/repos/{REPO}"): {"default_branch": "main"},
                ("GET", f"/repos/{REPO}/branches/main"): {"commit": {"id": "abc" * 13 + "a"}},
                ("POST", f"/repos/{REPO}/branches"): branch_conflict,
            }
        )

        # Should not raise
        provider.push_branch(REPO, "arvino/run-1/dev", PRINCIPAL)


class TestGiteaOpenPR:
    def test_returns_pull_request_info(self) -> None:
        provider = _make_gitea(
            {
                ("POST", f"/repos/{REPO}/pulls"): _PR_DATA,
            }
        )

        pr = provider.open_pull_request(REPO, "arvino/r1/pr", "main", "Title", "Body", PRINCIPAL)
        assert isinstance(pr, PullRequestInfo)
        assert pr.number == 5
        assert pr.head_ref == "arvino/r1/pr"
        assert pr.merged is False


class TestGiteaGetPR:
    def test_returns_pull_request_info(self) -> None:
        provider = _make_gitea(
            {
                ("GET", f"/repos/{REPO}/pulls/5"): {**_PR_DATA, "merged": True},
            }
        )

        pr = provider.get_pull_request(REPO, 5)
        assert pr.number == 5
        assert pr.merged is True


class TestGiteaChangedPaths:
    def test_single_page(self) -> None:
        files = [{"filename": "a.py"}, {"filename": "b.py"}]
        provider = _make_gitea(
            {
                ("GET", f"/repos/{REPO}/pulls/3/files"): files,
            }
        )

        paths = provider.changed_paths(REPO, 3)
        assert paths == ["a.py", "b.py"]

    def test_pagination(self) -> None:
        call_count = {"n": 0}

        def paginated(req: httpx.Request) -> httpx.Response:
            call_count["n"] += 1
            page = int(req.url.params.get("page", "1"))
            if page == 1:
                return httpx.Response(200, json=[{"filename": f"f{i}.py"} for i in range(50)])
            if page == 2:
                return httpx.Response(200, json=[{"filename": "last.py"}])
            return httpx.Response(200, json=[])

        provider = _make_gitea(
            {
                ("GET", f"/repos/{REPO}/pulls/3/files"): paginated,
            }
        )

        paths = provider.changed_paths(REPO, 3)
        assert len(paths) == 51
        assert paths[-1] == "last.py"


class TestGiteaHTTPErrors:
    def test_api_error_raises_httpx_error(self) -> None:
        def error_handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"message": "internal"})

        provider = _make_gitea(
            {
                ("GET", f"/repos/{REPO}/pulls/99"): error_handler,
            }
        )

        with pytest.raises(httpx.HTTPStatusError):
            provider.get_pull_request(REPO, 99)


class TestGiteaClose:
    def test_close_does_not_raise(self) -> None:
        provider = _make_gitea({})
        provider.close()  # smoke test
