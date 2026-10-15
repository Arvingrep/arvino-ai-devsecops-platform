from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from arvino.domain import ApprovalKind, InvalidWorkflowError, Profile, StepKind
from arvino.workflows.model import load_registry, parse_definition

REG = load_registry()


def base() -> dict[str, Any]:
    return {
        "profile": "fast",
        "description": "t",
        "steps": [
            {"key": "a", "title": "a", "kind": "agent", "role": "triage"},
            {"key": "b", "title": "b", "kind": "agent", "role": "developer", "depends_on": ["a"]},
        ],
    }


def test_all_three_profiles_load_and_are_acyclic() -> None:
    assert {w.profile for w in REG.all()} == set(Profile)
    for w in REG.all():
        order = w.topological_order()
        assert len(order) == len(w.steps)
        pos = {k: i for i, k in enumerate(order)}
        for s in w.steps:
            assert all(pos[d] < pos[s.key] for d in s.depends_on)


def test_defaults_are_filled() -> None:
    for w in REG.all():
        for s in w.steps:
            assert s.max_attempts >= 1 and s.timeout_seconds > 0


def test_cycle_rejected() -> None:
    raw = base()
    raw["steps"][0]["depends_on"] = ["b"]
    with pytest.raises(InvalidWorkflowError, match="cycle"):
        parse_definition(raw)


def test_unknown_dependency_and_duplicate_key_rejected() -> None:
    raw = base()
    raw["steps"][1]["depends_on"] = ["zzz"]
    with pytest.raises(InvalidWorkflowError, match="unknown dependency"):
        parse_definition(raw)
    raw = base()
    raw["steps"][1]["key"] = "a"
    with pytest.raises(InvalidWorkflowError, match="duplicate"):
        parse_definition(raw)


def test_agent_step_requires_role_and_approval_requires_kind() -> None:
    raw = base()
    del raw["steps"][0]["role"]
    with pytest.raises(InvalidWorkflowError):
        parse_definition(raw)
    raw = base()
    raw["steps"].append({"key": "c", "title": "c", "kind": "approval", "depends_on": ["b"]})
    with pytest.raises(InvalidWorkflowError):
        parse_definition(raw)


def test_standard_has_parallel_fanout_and_fanin() -> None:
    w = REG.get(Profile.STANDARD).by_key
    assert w["frontend"].depends_on == w["backend"].depends_on == ("architecture",)
    assert set(w["pull_request"].depends_on) == {"frontend", "backend"}
    assert w["qa"].depends_on == w["security"].depends_on == ("ci",)
    assert set(w["review"].depends_on) == {"qa", "security"}


def test_governed_has_two_owner_approvals_and_staging_before_production() -> None:
    g = REG.get(Profile.GOVERNED)
    approvals = [s for s in g.steps if s.kind is StepKind.APPROVAL]
    assert {a.approval_kind for a in approvals} == {
        ApprovalKind.ARCHITECTURE,
        ApprovalKind.PRODUCTION_DEPLOY,
    }
    assert "staging" in g.ancestors("production")
    assert "production_approval" in g.ancestors("production")
    assert "architecture_approval" in g.ancestors("implementation")
    assert {"qa", "security", "iac_review"} <= set(g.by_key["review"].depends_on)


def test_only_standard_and_governed_reach_production() -> None:
    for w in REG.all():
        prod = [s for s in w.steps if s.kind is StepKind.DEPLOY and s.environment == "production"]
        assert bool(prod) == (w.profile is not Profile.FAST)


def test_every_profile_gates_deploys_behind_ci_and_gate() -> None:
    for w in REG.all():
        for s in w.steps:
            if s.kind is StepKind.DEPLOY:
                kinds = {w.by_key[a].kind for a in w.ancestors(s.key)}
                assert {StepKind.CI, StepKind.GATE} <= kinds


# ---- safety lint: definitions that would weaken gates are rejected at load time


def _standard() -> dict[str, Any]:
    import yaml

    from arvino.workflows import model

    path = model.resources.files("arvino.workflows").joinpath("definitions/standard.yaml")
    return yaml.safe_load(path.read_text())  # type: ignore[no-any-return]


def test_lint_rejects_production_without_approval() -> None:
    raw = deepcopy(_standard())
    for s in raw["steps"]:
        if s["key"] == "release":
            s["depends_on"] = ["preview"]
    with pytest.raises(InvalidWorkflowError, match="production-deploy approval"):
        parse_definition(raw)


def test_lint_rejects_deploy_without_gate() -> None:
    raw = deepcopy(_standard())
    for s in raw["steps"]:
        if s["key"] == "preview":
            s["depends_on"] = ["ci"]
    with pytest.raises(InvalidWorkflowError, match="gate"):
        parse_definition(raw)


def test_lint_rejects_governed_implementation_without_architecture_approval() -> None:
    import yaml

    from arvino.workflows import model

    raw = yaml.safe_load(
        model.resources.files("arvino.workflows").joinpath("definitions/governed.yaml").read_text()
    )
    for s in raw["steps"]:
        if s["key"] == "implementation":
            s["depends_on"] = ["adr"]
    with pytest.raises(InvalidWorkflowError, match="architecture approval"):
        parse_definition(raw)


# ---- validate_file / validate_directory (custom user-submitted workflows)


def test_validate_file_rejects_deploy_without_gate(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(
        """\
profile: fast
description: deploy without gate
steps:
  - {key: a, title: a, kind: agent, role: triage}
  - {key: b, title: b, kind: deploy, environment: preview, depends_on: [a]}
"""
    )
    with pytest.raises(InvalidWorkflowError, match="gate"):
        from arvino.workflows.model import validate_file

        validate_file(p)


def test_validate_file_rejects_bad_yaml(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(": : :")
    with pytest.raises(InvalidWorkflowError, match="invalid YAML"):
        from arvino.workflows.model import validate_file

        validate_file(p)


def test_validate_file_rejects_non_mapping(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text("- not a mapping")
    with pytest.raises(InvalidWorkflowError, match="YAML mapping"):
        from arvino.workflows.model import validate_file

        validate_file(p)


def test_validate_file_accepts_valid_workflow(tmp_path: Path) -> None:
    p = tmp_path / "good.yaml"
    p.write_text(
        """\
profile: fast
description: valid workflow
steps:
  - {key: a, title: a, kind: agent, role: triage}
"""
    )
    from arvino.workflows.model import validate_file

    wf = validate_file(p)
    assert wf.profile.value == "fast"
    assert len(wf.steps) == 1


def test_validate_directory_skips_non_yaml(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("not yaml")
    p = tmp_path / "good.yaml"
    p.write_text(
        """\
profile: fast
description: valid
steps:
  - {key: a, title: a, kind: agent, role: triage}
"""
    )
    from arvino.workflows.model import validate_directory

    results = validate_directory(tmp_path)
    assert list(results.keys()) == ["good.yaml"]
    assert results["good.yaml"].profile.value == "fast"


def test_validate_directory_rejects_any_invalid(tmp_path: Path) -> None:
    (tmp_path / "good.yaml").write_text(
        """\
profile: fast
description: valid
steps:
  - {key: a, title: a, kind: agent, role: triage}
"""
    )
    (tmp_path / "bad.yaml").write_text(
        """\
profile: fast
description: deploy without gate
steps:
  - {key: a, title: a, kind: deploy, environment: preview}
"""
    )
    from arvino.workflows.model import validate_directory

    with pytest.raises(InvalidWorkflowError, match="gate"):
        validate_directory(tmp_path)
