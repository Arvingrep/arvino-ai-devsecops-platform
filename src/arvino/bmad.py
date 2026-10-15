"""BMAD v6.12 artifact helpers for the run ledger.

When a product (``spec``) or architect (``architecture`` / ``adr``) step
completes it should register its BMAD output files as :class:`~arvino.domain.ArtifactSpec`
entries in the ledger.  Downstream steps then receive them via
``StepRequest.upstream_artifacts``, giving the architect the PRD, the developer
the architecture doc, and so on — all fully referenced and immutable.

URI scheme
----------
``git://<repo_full_name>/<git_ref>/<path_in_repo>``

Example::

    git://Arvingrep/firebird-sea/main/_bmad-output/planning-artifacts/prds/prd.md

This is a logical pointer, not a fetchable URL; the executor resolves it via
``git show <git_ref>:<path_in_repo>`` inside the project repository.

Canonical artifact names
------------------------
The ledger's uniqueness constraint is ``(run_id, step_key, name)``.  Use the
filename stem so that architecture reviewers can find the PRD by name
(e.g. ``"prd"`` for ``prd.md``, ``"architecture-spine"`` for
``ARCHITECTURE-SPINE.md``).  For stories use the story-id key, e.g. ``"2-1"``.

Canonical names per BMAD output directory layout::

    _bmad-output/planning-artifacts/prds/<dir>/prd.md        → name="prd"
    _bmad-output/planning-artifacts/architecture/<dir>/*.md   → name=stem (e.g. "ARCHITECTURE-SPINE")
    _bmad-output/planning-artifacts/briefs/<dir>/brief.md     → name="brief"
    _bmad-output/implementation-artifacts/spec-<N>-<M>-*.md  → name="<N>-<M>"
"""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any

from arvino.domain import ArtifactSpec, BmadArtifactKind

# ---------------------------------------------------------------------------
# URI helpers
# ---------------------------------------------------------------------------

_STORY_RE = re.compile(r"spec-(\d+-\d+)-")


def bmad_uri(repo_full_name: str, git_ref: str, path_in_repo: str) -> str:
    """Build a canonical BMAD git URI."""
    return f"git://{repo_full_name}/{git_ref}/{path_in_repo.lstrip('/')}"


# ---------------------------------------------------------------------------
# ArtifactSpec builders
# ---------------------------------------------------------------------------


def prd_artifact(
    repo_full_name: str,
    git_ref: str,
    path_in_repo: str,
    *,
    sha256: str | None = None,
    extra: dict[str, Any] | None = None,
    name: str = "prd",
) -> ArtifactSpec:
    """Build an ArtifactSpec for a BMAD PRD file."""
    return ArtifactSpec(
        kind=BmadArtifactKind.PRD,
        name=name,
        uri=bmad_uri(repo_full_name, git_ref, path_in_repo),
        sha256=sha256,
        metadata={"repo": repo_full_name, "ref": git_ref, "path": path_in_repo, **(extra or {})},
    )


def architecture_artifact(
    repo_full_name: str,
    git_ref: str,
    path_in_repo: str,
    *,
    sha256: str | None = None,
    extra: dict[str, Any] | None = None,
    name: str | None = None,
) -> ArtifactSpec:
    """Build an ArtifactSpec for a BMAD architecture / ADR file."""
    stem = PurePosixPath(path_in_repo).stem
    resolved_name: str = name if name is not None else stem
    return ArtifactSpec(
        kind=BmadArtifactKind.ARCHITECTURE,
        name=resolved_name,
        uri=bmad_uri(repo_full_name, git_ref, path_in_repo),
        sha256=sha256,
        metadata={"repo": repo_full_name, "ref": git_ref, "path": path_in_repo, **(extra or {})},
    )


def story_artifact(
    repo_full_name: str,
    git_ref: str,
    path_in_repo: str,
    *,
    sha256: str | None = None,
    extra: dict[str, Any] | None = None,
    name: str | None = None,
) -> ArtifactSpec:
    """Build an ArtifactSpec for a BMAD story spec file.

    If *name* is omitted it is derived from the story-id in the filename
    (``spec-2-1-payments.md`` → ``"2-1"``).
    """
    stem = PurePosixPath(path_in_repo).name
    m = _STORY_RE.search(stem)
    resolved_name: str = name if name is not None else (m.group(1) if m else PurePosixPath(path_in_repo).stem)
    return ArtifactSpec(
        kind=BmadArtifactKind.STORY,
        name=resolved_name,
        uri=bmad_uri(repo_full_name, git_ref, path_in_repo),
        sha256=sha256,
        metadata={"repo": repo_full_name, "ref": git_ref, "path": path_in_repo, **(extra or {})},
    )


def brief_artifact(
    repo_full_name: str,
    git_ref: str,
    path_in_repo: str,
    *,
    sha256: str | None = None,
    extra: dict[str, Any] | None = None,
    name: str = "brief",
) -> ArtifactSpec:
    """Build an ArtifactSpec for a BMAD project brief file."""
    return ArtifactSpec(
        kind=BmadArtifactKind.BRIEF,
        name=name,
        uri=bmad_uri(repo_full_name, git_ref, path_in_repo),
        sha256=sha256,
        metadata={"repo": repo_full_name, "ref": git_ref, "path": path_in_repo, **(extra or {})},
    )


# ---------------------------------------------------------------------------
# SHA-256 utility (for testing and local git-object hashing)
# ---------------------------------------------------------------------------


def sha256_of_text(content: str) -> str:
    """Return the SHA-256 hex digest of *content* encoded as UTF-8."""
    return hashlib.sha256(content.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Convenience: filter upstream artifacts by BMAD kind
# ---------------------------------------------------------------------------


def filter_bmad(
    upstream: list[ArtifactSpec],
    kind: BmadArtifactKind,
) -> list[ArtifactSpec]:
    """Return only the upstream artifacts that match the given BMAD kind."""
    return [a for a in upstream if a.kind == kind]
