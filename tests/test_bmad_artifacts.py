"""BMAD artifact integration tests.

Verifies that:
1. ``BmadArtifactKind`` values are distinct and stable.
2. The ``arvino.bmad`` helpers produce correctly typed ``ArtifactSpec`` objects.
3. In the Standard workflow, BMAD artifacts registered by ``spec`` flow as
   ``upstream_artifacts`` to the ``architecture`` step; artifacts registered by
   ``architecture`` flow to ``frontend``, ``backend``, and ``pull_request``.
4. In the Governed workflow, ``spec`` artifacts reach ``adr``; ``adr`` artifacts
   reach ``implementation``.
5. Multiple BMAD artifact kinds can coexist within a single step.
6. ``filter_bmad`` returns only the requested kind.
"""

from __future__ import annotations

from arvino.bmad import (
    architecture_artifact,
    bmad_uri,
    brief_artifact,
    filter_bmad,
    prd_artifact,
    sha256_of_text,
    story_artifact,
)
from arvino.domain import ArtifactSpec, BmadArtifactKind, Profile
from arvino.ports import StepOutcome, StepRequest
from tests.conftest import Env

_REPO = "Arvingrep/firebird-sea"
_REF = "main"
_SHA = "a" * 64  # 64-char hex string satisfying the sha256 pattern


# ---------------------------------------------------------------------------
# Unit tests: BmadArtifactKind values
# ---------------------------------------------------------------------------


def test_bmad_kind_values_are_stable() -> None:
    assert BmadArtifactKind.PRD == "bmad-prd"
    assert BmadArtifactKind.ARCHITECTURE == "bmad-architecture"
    assert BmadArtifactKind.STORY == "bmad-story"
    assert BmadArtifactKind.BRIEF == "bmad-brief"


def test_bmad_kind_values_are_distinct() -> None:
    kinds = list(BmadArtifactKind)
    assert len(kinds) == len(set(kinds))


# ---------------------------------------------------------------------------
# Unit tests: artifact builder helpers
# ---------------------------------------------------------------------------


def test_prd_artifact_fields() -> None:
    a = prd_artifact(
        _REPO, _REF, "_bmad-output/planning-artifacts/prds/prd-2026/prd.md", sha256=_SHA
    )
    assert a.kind == BmadArtifactKind.PRD
    assert a.name == "prd"
    assert a.uri == f"git://{_REPO}/{_REF}/_bmad-output/planning-artifacts/prds/prd-2026/prd.md"
    assert a.sha256 == _SHA
    assert a.metadata["repo"] == _REPO
    assert a.metadata["ref"] == _REF


def test_architecture_artifact_derives_name_from_stem() -> None:
    a = architecture_artifact(
        _REPO, _REF, "_bmad-output/planning-artifacts/architecture/x/ARCHITECTURE-SPINE.md"
    )
    assert a.kind == BmadArtifactKind.ARCHITECTURE
    assert a.name == "ARCHITECTURE-SPINE"


def test_architecture_artifact_explicit_name() -> None:
    a = architecture_artifact(
        _REPO,
        _REF,
        "_bmad-output/planning-artifacts/architecture/x/ARCHITECTURE-SPINE.md",
        name="arch",
    )
    assert a.name == "arch"


def test_story_artifact_derives_id_from_filename() -> None:
    a = story_artifact(
        _REPO, _REF, "_bmad-output/implementation-artifacts/spec-2-1-payments-core.md"
    )
    assert a.kind == BmadArtifactKind.STORY
    assert a.name == "2-1"


def test_story_artifact_explicit_name() -> None:
    a = story_artifact(
        _REPO,
        _REF,
        "_bmad-output/implementation-artifacts/spec-2-1-payments.md",
        name="payments-core",
    )
    assert a.name == "payments-core"


def test_brief_artifact_fields() -> None:
    a = brief_artifact(_REPO, _REF, "_bmad-output/planning-artifacts/briefs/b/brief.md")
    assert a.kind == BmadArtifactKind.BRIEF
    assert a.name == "brief"


def test_bmad_uri_no_leading_slash() -> None:
    uri = bmad_uri(_REPO, _REF, "/_bmad-output/prd.md")
    assert not uri.startswith("git:///")
    assert uri == f"git://{_REPO}/{_REF}/_bmad-output/prd.md"


def test_sha256_of_text_returns_64_hex_chars() -> None:
    h = sha256_of_text("hello world")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_artifactspec_rejects_bad_sha256() -> None:
    import pytest

    with pytest.raises(ValueError):
        ArtifactSpec(kind="bmad-prd", name="prd", uri="git://x/y", sha256="tooshort")


# ---------------------------------------------------------------------------
# Unit tests: filter_bmad
# ---------------------------------------------------------------------------


def test_filter_bmad_returns_only_matching_kind() -> None:
    prd = prd_artifact(_REPO, _REF, "_bmad-output/prds/prd.md")
    arch = architecture_artifact(_REPO, _REF, "_bmad-output/arch/ARCHITECTURE-SPINE.md")
    story = story_artifact(_REPO, _REF, "_bmad-output/impl/spec-2-1-payments.md")
    upstream = [prd, arch, story]

    assert filter_bmad(upstream, BmadArtifactKind.PRD) == [prd]
    assert filter_bmad(upstream, BmadArtifactKind.ARCHITECTURE) == [arch]
    assert filter_bmad(upstream, BmadArtifactKind.STORY) == [story]
    assert filter_bmad(upstream, BmadArtifactKind.BRIEF) == []


# ---------------------------------------------------------------------------
# Integration tests: Standard workflow — artifact flow
# ---------------------------------------------------------------------------

_PRD = prd_artifact(_REPO, _REF, "_bmad-output/planning-artifacts/prds/prd.md", sha256=_SHA)
_ARCH = architecture_artifact(
    _REPO,
    _REF,
    "_bmad-output/planning-artifacts/architecture/x/ARCHITECTURE-SPINE.md",
    sha256="b" * 64,
)


def _spec_executor(req: StepRequest) -> StepOutcome:
    """Simulates the product step registering the PRD as a BMAD artifact."""
    return StepOutcome(status="succeeded", artifacts=[_PRD])


def _architecture_executor(req: StepRequest) -> StepOutcome:
    """Simulates the architect step consuming the PRD and registering the arch artifact."""
    # Verify the PRD was delivered as upstream
    assert any(
        a.kind == BmadArtifactKind.PRD and a.name == "prd" for a in req.upstream_artifacts
    ), f"architecture step did not receive PRD; upstream={req.upstream_artifacts}"
    return StepOutcome(status="succeeded", artifacts=[_ARCH])


def _implementer_executor(req: StepRequest) -> StepOutcome:
    """Simulates a frontend/backend step consuming the architecture artifact."""
    assert any(a.kind == BmadArtifactKind.ARCHITECTURE for a in req.upstream_artifacts), (
        f"implementer step did not receive architecture artifact; upstream={req.upstream_artifacts}"
    )
    return StepOutcome(status="running")


def test_standard_spec_prd_artifact_reaches_architecture(env: Env) -> None:
    """Standard workflow: PRD produced by spec is passed to architecture as upstream."""
    env.executor.script("spec", _spec_executor)
    env.executor.script("architecture", _architecture_executor)
    env.executor.script("frontend", StepOutcome(status="running"))
    env.executor.script("backend", StepOutcome(status="running"))

    run = env.start(Profile.STANDARD)

    # spec and architecture should already be done (synchronous executors)
    assert run.statuses()["spec"] == "succeeded"
    assert run.statuses()["architecture"] == "succeeded"

    # The PRD artifact is in the ledger under the spec step
    prd_in_ledger = [a for a in run.artifacts if a.kind == BmadArtifactKind.PRD]
    assert len(prd_in_ledger) == 1
    assert prd_in_ledger[0].name == "prd"
    assert prd_in_ledger[0].step_key == "spec"


def test_standard_architecture_artifact_reaches_frontend_and_backend(env: Env) -> None:
    """Standard workflow: arch artifact produced by architecture is passed to frontend/backend."""
    env.executor.script("spec", _spec_executor)
    env.executor.script("architecture", _architecture_executor)
    env.executor.script("frontend", _implementer_executor)
    env.executor.script("backend", _implementer_executor)

    run = env.start(Profile.STANDARD)

    # Both frontend and backend should be running (the executor returns "running")
    assert run.statuses()["frontend"] == "running"
    assert run.statuses()["backend"] == "running"

    # Both were called (assertions inside the executors would have raised if upstream missing)
    assert len(env.executor.calls_for("frontend")) == 1
    assert len(env.executor.calls_for("backend")) == 1


def test_standard_both_bmad_artifacts_in_ledger(env: Env) -> None:
    """After spec and architecture complete, the run ledger holds both BMAD artifacts."""
    env.executor.script("spec", _spec_executor)
    env.executor.script("architecture", _architecture_executor)

    run = env.start(Profile.STANDARD)

    bmad_artifacts = [a for a in run.artifacts if a.kind.startswith("bmad-")]
    kinds_in_ledger = {a.kind for a in bmad_artifacts}
    assert BmadArtifactKind.PRD in kinds_in_ledger
    assert BmadArtifactKind.ARCHITECTURE in kinds_in_ledger


def test_standard_only_direct_upstream_artifacts_flow(env: Env) -> None:
    """architecture only receives artifacts from spec (its direct dep), not from unrelated steps."""
    env.executor.script("spec", _spec_executor)

    captured: list[list[ArtifactSpec]] = []

    def _capture_upstream(req: StepRequest) -> StepOutcome:
        captured.append(list(req.upstream_artifacts))
        return StepOutcome(status="succeeded", artifacts=[_ARCH])

    env.executor.script("architecture", _capture_upstream)
    env.start(Profile.STANDARD)

    assert len(captured) == 1
    # The only upstream step for architecture is spec
    assert all(a.kind == BmadArtifactKind.PRD for a in captured[0])


# ---------------------------------------------------------------------------
# Integration tests: Governed workflow — artifact flow
# ---------------------------------------------------------------------------

_GOVERNED_PRD = prd_artifact(
    _REPO, _REF, "_bmad-output/planning-artifacts/prds/prd.md", sha256="c" * 64
)
_GOVERNED_ARCH = architecture_artifact(
    _REPO,
    _REF,
    "_bmad-output/planning-artifacts/architecture/x/ARCHITECTURE-SPINE.md",
    sha256="d" * 64,
)


def test_governed_spec_artifact_reaches_adr(env: Env) -> None:
    """Governed workflow: PRD produced by spec flows to the adr step."""
    env.executor.script("research", StepOutcome(status="succeeded"))
    env.executor.script("spec", StepOutcome(status="succeeded", artifacts=[_GOVERNED_PRD]))

    adr_received_prd: list[bool] = []

    def _adr_executor(req: StepRequest) -> StepOutcome:
        adr_received_prd.append(any(a.kind == BmadArtifactKind.PRD for a in req.upstream_artifacts))
        return StepOutcome(status="succeeded", artifacts=[_GOVERNED_ARCH])

    env.executor.script("adr", _adr_executor)

    run = env.start(Profile.GOVERNED)

    assert run.statuses()["adr"] == "succeeded"
    assert adr_received_prd == [True], "adr step did not receive PRD artifact"


def test_governed_adr_artifact_reaches_implementation_after_approval(env: Env) -> None:
    """Governed workflow: arch artifact from adr flows to implementation after arch approval."""
    from tests.conftest import OWNER

    env.executor.script("research", StepOutcome(status="succeeded"))
    env.executor.script("spec", StepOutcome(status="succeeded", artifacts=[_GOVERNED_PRD]))
    env.executor.script("adr", StepOutcome(status="succeeded", artifacts=[_GOVERNED_ARCH]))

    impl_received_arch: list[bool] = []

    def _impl_executor(req: StepRequest) -> StepOutcome:
        impl_received_arch.append(
            any(a.kind == BmadArtifactKind.ARCHITECTURE for a in req.upstream_artifacts)
        )
        return StepOutcome(status="running")

    env.executor.script("implementation", _impl_executor)

    run = env.start(Profile.GOVERNED)
    # architecture_approval is pending at this point
    assert run.statuses()["architecture_approval"] == "waiting_approval"

    # Approve the architecture — this unblocks implementation
    env.approve(run.id, "architecture_approval", OWNER)
    run = env.engine.get_run(run.id)

    assert run.statuses()["implementation"] == "running"
    assert impl_received_arch == [True], "implementation step did not receive architecture artifact"


# ---------------------------------------------------------------------------
# Integration tests: multiple BMAD artifact kinds from one step
# ---------------------------------------------------------------------------


def test_spec_step_can_register_prd_and_brief_together(env: Env) -> None:
    """A product step may register both a PRD and a brief in a single completion."""
    _brief = brief_artifact(
        _REPO, _REF, "_bmad-output/planning-artifacts/briefs/b/brief.md", sha256="e" * 64
    )

    env.executor.script(
        "spec",
        StepOutcome(status="succeeded", artifacts=[_PRD, _brief]),
    )

    run = env.start(Profile.STANDARD)

    artifact_kinds = {a.kind for a in run.artifacts}
    assert BmadArtifactKind.PRD in artifact_kinds
    assert BmadArtifactKind.BRIEF in artifact_kinds


def test_architecture_step_can_register_arch_and_story_together(env: Env) -> None:
    """An architect step may register arch doc and a first story spec together."""
    _story = story_artifact(
        _REPO,
        _REF,
        "_bmad-output/implementation-artifacts/spec-2-1-payments-core.md",
        sha256="f" * 64,
    )

    env.executor.script("spec", _spec_executor)
    env.executor.script(
        "architecture",
        StepOutcome(status="succeeded", artifacts=[_ARCH, _story]),
    )

    run = env.start(Profile.STANDARD)

    artifact_kinds = {a.kind for a in run.artifacts}
    assert BmadArtifactKind.ARCHITECTURE in artifact_kinds
    assert BmadArtifactKind.STORY in artifact_kinds
    story_in_ledger = [a for a in run.artifacts if a.kind == BmadArtifactKind.STORY]
    assert story_in_ledger[0].name == "2-1"
