"""End-to-end: the engine against the *real* Rego policy (opa binary), plus OPA HTTP client.

Skipped only when no `opa` binary is available; CI always installs one.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from arvino.domain import PolicyDeniedError, Profile
from arvino.policy import FailClosedPolicy, OpaCliPolicy, OpaHttpPolicy
from tests.conftest import ALL_CHECKS, BASE_CHECKS, OWNER, ROOT, SHA, Env, build_env

POLICY_DIR = ROOT / "policy"


def _find_opa() -> str | None:
    cand = os.environ.get("ARVINO_OPA_BIN") or shutil.which("opa")
    local = ROOT / ".runtime" / "tools" / "bin" / "opa"
    return cand or (str(local) if local.exists() else None)


OPA = _find_opa()
pytestmark = [pytest.mark.opa, pytest.mark.skipif(OPA is None, reason="opa binary not available")]


@pytest.fixture(scope="module")
def policy() -> FailClosedPolicy:
    assert OPA
    return FailClosedPolicy(OpaCliPolicy(POLICY_DIR, opa_bin=OPA))


def run_to_gate(env: Env, paths: list[str], profile: Profile = Profile.FAST) -> str:
    env.executor.script("pull_request", env.pr_script(paths))
    run = env.start(profile)
    if profile is Profile.GOVERNED:
        env.approve(run.id, "architecture_approval")
    env.webhook_pr(run.id)
    env.checks(run.id, BASE_CHECKS if profile is Profile.FAST else ALL_CHECKS)
    return run.id


def test_clean_fast_run_passes_real_policy(policy: FailClosedPolicy) -> None:
    env = build_env(policy=policy)
    run_id = run_to_gate(env, ["src/app.py"])
    run = env.engine.get_run(run_id)
    assert run.step("merge_gate").status == "succeeded"
    assert run.step("preview").status == "running"


@pytest.mark.parametrize(
    "path",
    ["policy/authz.rego", ".gitea/workflows/ci.yaml", ".github/workflows/ci.yml", "CODEOWNERS"],
)
def test_protected_paths_are_denied_for_every_profile(policy: FailClosedPolicy, path: str) -> None:
    env = build_env(policy=policy)
    run_id = run_to_gate(env, ["src/ok.py", path])
    run = env.engine.get_run(run_id)
    assert run.step("merge_gate").status == "failed"
    assert "protected from agent changes" in (run.step("merge_gate").last_error or "")
    assert env.promoter.requests == []


def test_governed_architecture_approval_does_not_unlock_forbidden_paths(
    policy: FailClosedPolicy,
) -> None:
    env = build_env(policy=policy)
    run_id = run_to_gate(env, ["policy/config.rego"], Profile.GOVERNED)
    assert env.engine.get_run(run_id).step("merge_gate").status == "failed"


def test_restricted_path_needs_architecture_approval(policy: FailClosedPolicy) -> None:
    env = build_env(policy=policy)
    run_id = run_to_gate(
        env, ["terraform/main.tf"], Profile.STANDARD
    )  # standard has no architecture approval
    run = env.engine.get_run(run_id)
    assert run.step("merge_gate").status == "failed"
    assert "architecture approval" in (run.step("merge_gate").last_error or "")

    env2 = build_env(policy=policy)
    run_id2 = run_to_gate(
        env2, ["terraform/main.tf"], Profile.GOVERNED
    )  # governed: owner approved the ADR
    assert env2.engine.get_run(run_id2).step("merge_gate").status == "succeeded"


def test_real_policy_denies_agent_and_self_approval(policy: FailClosedPolicy) -> None:
    from arvino.domain import Principal, PrincipalKind

    env = build_env(policy=policy)
    run = env.start(Profile.GOVERNED)
    appr = run.approvals[0]
    agent = Principal(id="agent:architect", kind=PrincipalKind.AGENT, roles=("owner",))
    with pytest.raises(PolicyDeniedError):
        env.engine.decide_approval(appr.id, agent, "approved")
    env.engine.decide_approval(appr.id, OWNER, "approved")
    assert env.engine.get_run(run.id).step("architecture_approval").status == "succeeded"


def test_full_standard_release_with_real_policy(policy: FailClosedPolicy) -> None:
    env = build_env(policy=policy)
    run_id = run_to_gate(env, ["src/app.py"], Profile.STANDARD)
    env.converge("acme-preview")
    env.engine.update_git_state(
        run_id, merged=True, merge_sha="b" * 40, merged_by="owner", source="test"
    )
    assert env.engine.get_run(run_id).step("release_approval").status == "waiting_approval"
    env.approve(run_id, "release_approval")
    assert env.engine.get_run(run_id).step("release").status == "running"
    env.converge("acme-prod", revision="b" * 40)
    assert env.engine.get_run(run_id).status == "succeeded"


def test_missing_policy_directory_fails_closed(tmp_path: Path) -> None:
    assert OPA
    broken = FailClosedPolicy(OpaCliPolicy(tmp_path / "nope", opa_bin=OPA))
    env = build_env(policy=broken)
    run_id = run_to_gate(env, ["src/app.py"])
    assert env.engine.get_run(run_id).step("merge_gate").status == "failed"
    assert env.promoter.requests == []


# ---------------------------------------------------------------- OPA HTTP client against a real server


@pytest.fixture(scope="module")
def opa_server() -> Iterator[str]:
    assert OPA
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(  # noqa: S603
        [OPA, "run", "--server", f"--addr=127.0.0.1:{port}", str(POLICY_DIR)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/health", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            pytest.fail("opa server did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_http_client_allows_and_denies(opa_server: str) -> None:
    pol = FailClosedPolicy(OpaHttpPolicy(opa_server))
    ok = pol.decide(
        {
            "action": "git.write",
            "principal": {"id": "agent:developer", "kind": "agent", "roles": []},
            "git": {"operation": "push", "branch": f"arvino/run-1/{SHA[:4]}"},
        }
    )
    assert ok.allow
    bad = pol.decide(
        {
            "action": "git.write",
            "principal": {"id": "agent:developer", "kind": "agent", "roles": []},
            "git": {"operation": "merge", "branch": "main"},
        }
    )
    assert not bad.allow and any("merge" in r for r in bad.reasons)


def test_http_client_fails_closed_when_server_unreachable() -> None:
    pol = FailClosedPolicy(OpaHttpPolicy("http://127.0.0.1:1", timeout=0.3))
    d = pol.decide({"action": "git.write"})
    assert not d.allow and "policy unavailable" in d.reasons[0]


def test_engine_against_http_opa(opa_server: str) -> None:
    env = build_env(policy=FailClosedPolicy(OpaHttpPolicy(opa_server)))
    run_id = run_to_gate(env, ["src/app.py"])
    assert env.engine.get_run(run_id).step("merge_gate").status == "succeeded"
