from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arvino import cli
from arvino.domain import PolicyDeniedError
from arvino.fakes import FakeGit, StaticPolicy
from arvino.git_guard import GuardedGitProvider
from arvino.ports import GitProvider
from tests.conftest import AGENT, OWNER, ROOT
from tests.test_policy_opa import OPA


def test_git_provider_port_has_no_merge_operation() -> None:
    """Structural guarantee: no code path through the port can merge a pull request."""
    names = {n for n in dir(GitProvider) if not n.startswith("_")}
    assert not any("merge" in n for n in names)
    assert not any(
        "merge" in n for n in dir(FakeGit) if not n.startswith("_") and n != "register_pr"
    )


def test_guard_delegates_only_when_policy_allows() -> None:
    inner, pol = FakeGit(), StaticPolicy(allow=True)
    guard = GuardedGitProvider(inner, pol)
    guard.push_branch("acme/app", "arvino/run-1/x", AGENT)
    assert inner.pushed == [("acme/app", "arvino/run-1/x", AGENT.id)]
    assert pol.inputs[0]["git"] == {"operation": "push", "branch": "arvino/run-1/x"}
    pol.allow, pol.reasons = False, ["nope"]
    with pytest.raises(PolicyDeniedError):
        guard.push_branch("acme/app", "main", AGENT)
    with pytest.raises(PolicyDeniedError):
        guard.open_pull_request("acme/app", "main", "main", "t", "b", AGENT)
    assert len(inner.pushed) == 1


@pytest.mark.skipif(OPA is None, reason="opa binary not available")
def test_guard_with_real_policy() -> None:
    from arvino.policy import FailClosedPolicy, OpaCliPolicy

    assert OPA
    guard = GuardedGitProvider(
        FakeGit(), FailClosedPolicy(OpaCliPolicy(ROOT / "policy", opa_bin=OPA))
    )
    guard.push_branch("acme/app", "arvino/run-1/x", AGENT)
    for branch in ("main", "release/1.0", "feature/x"):
        with pytest.raises(PolicyDeniedError):
            guard.push_branch("acme/app", branch, AGENT)
    guard.push_branch("acme/app", "main", OWNER)  # humans are not constrained by the agent rules


def test_cli_validate_workflows_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["validate-workflows"]) == 0
    out = capsys.readouterr().out
    assert all(p in out for p in ("fast", "standard", "governed"))


def test_cli_validate_workflows_rejects_unsafe_definition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src = ROOT / "src" / "arvino" / "workflows" / "definitions"
    for f in src.glob("*.yaml"):
        (tmp_path / f.name).write_text(f.read_text())
    std = yaml.safe_load((tmp_path / "standard.yaml").read_text())
    for s in std["steps"]:
        if s["key"] == "release":
            s["depends_on"] = ["preview"]  # drop the approval
    (tmp_path / "standard.yaml").write_text(yaml.safe_dump(std))
    assert cli.main(["validate-workflows", str(tmp_path)]) == 1
    assert "production-deploy approval" in capsys.readouterr().err


def test_cli_reconcile_and_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ARVINO_DATABASE_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    monkeypatch.setenv("ARVINO_POLICY_DIR", str(ROOT / "policy"))
    assert cli.main(["reconcile"]) == 0
    assert '"advanced": []' in capsys.readouterr().out
    assert cli.main(["list-runs"]) == 0
