"""Argo CD GitOpsPromoter adapter — commits to the homelab GitOps repo only.

Implements :class:`~arvino.ports.GitOpsPromoter` by modifying Kubernetes
manifests in the homelab GitOps repository (gitea_admin/homelab on
git.k8shome.com) and pushing the change.  Argo CD auto-sync detects the
commit through its configured repository polling; this adapter deliberately
never calls ``argocd app sync``.

Environment variable reference
------------------------------
ARVINO_GITOPS_REPO_URL
    Clone URL of the GitOps repo, e.g.
    ``https://git.k8shome.com/gitea_admin/homelab.git``.
ARVINO_GITOPS_TOKEN
    Gitea personal-access or application token with ``repo`` scope.
ARVINO_GITOPS_CACHE_DIR
    Optional directory to cache the clone between promotions.  When
    unset a fresh shallow clone is created for every call.
ARVINO_GITOPS_BRANCH
    Branch to commit to (default ``main``).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from arvino.ports import PromotionRequest, PromotionResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------

_DEFAULT_BRANCH = "main"
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_Dumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


# ---------------------------------------------------------------------------
# public adapter
# ---------------------------------------------------------------------------


class ArgoCDPromoter:
    """Commit an image promotion to the homelab GitOps repository.

    Parameters
    ----------
    repo_url:
        Clone URL of the GitOps repo (e.g.
        ``https://git.k8shome.com/gitea_admin/homelab.git``).
    token:
        Gitea token with ``repo`` scope.  Injected into the clone URL as
        the userinfo component so ``git push`` authenticates out of band.
    cache_dir:
        Optional directory for a persistent bare clone.  When provided the
        first call does a full clone; subsequent calls ``git fetch`` then
        create a temporary worktree, which is much faster than a full clone
        for large repos.  When ``None`` (default) a fresh shallow clone is
        performed on every ``request_promotion`` call.
    branch:
        Branch to commit and push to (default ``main``).
    """

    def __init__(
        self,
        repo_url: str,
        token: str,
        *,
        cache_dir: str | None = None,
        branch: str = _DEFAULT_BRANCH,
    ) -> None:
        self._repo_url = repo_url
        self._authenticated_url = _inject_token(repo_url, token)
        self._branch = branch
        self._cache_dir = Path(cache_dir) if cache_dir else None

    # -- GitOpsPromoter ---------------------------------------------------

    def request_promotion(self, request: PromotionRequest) -> PromotionResult:
        """Update the app's image tag in the GitOps repo and push.

        The commit lands on ``self._branch``.  Argo CD will detect it
        through its configured polling/notification channel (already
        relayed via n8n); this method never calls ``argocd`` CLI.
        """
        if not request.app:
            raise ValueError("PromotionRequest.app is required for ArgoCDPromoter")
        if not request.revision:
            raise ValueError("PromotionRequest.revision is required for ArgoCDPromoter")

        clone_path = self._prepare_clone()
        try:
            manifest_path = self._resolve_manifest_path(
                clone_path, request.app, request.environment
            )
            _set_image_tag(manifest_path, request.revision)

            commit_sha = self._commit_and_push(
                clone_path,
                message=(
                    f"promote({request.app}/{request.environment}): "
                    f"{request.revision}\n\n"
                    f"run_id: {request.run_id}\n"
                    f"project: {request.project_key}"
                ),
                run_id=request.run_id,
            )

            return PromotionResult(
                reference=commit_sha,
                detail={
                    "app": request.app,
                    "environment": request.environment,
                    "revision": request.revision,
                    "manifest": str(
                        manifest_path.relative_to(clone_path)
                        if manifest_path.is_relative_to(clone_path)
                        else manifest_path
                    ),
                },
            )
        finally:
            if self._cache_dir is None:
                shutil.rmtree(clone_path, ignore_errors=True)

    # -- internal ---------------------------------------------------------

    def _prepare_clone(self) -> Path:
        """Return a writable path holding the current HEAD of the GitOps repo."""
        if self._cache_dir is not None:
            return self._clone_cached()
        return self._clone_shallow()

    def _clone_shallow(self) -> Path:
        """Fresh shallow clone — simple, no cache coordination needed."""
        tmp = Path(tempfile.mkdtemp(prefix="gitops-"))
        _git(
            "clone",
            "--depth=1",
            "--single-branch",
            "--branch",
            self._branch,
            self._authenticated_url,
            str(tmp),
        )
        logger.debug("shallow clone -> %s", tmp)
        return tmp

    def _clone_cached(self) -> Path:
        """Return a worktree from a cached bare clone (fast path)."""
        bare = self._cache_dir / "bare"  # type: ignore[union-attr]
        if not (bare / "HEAD").exists():
            _git(
                "clone", "--bare", "--single-branch", "--branch", self._branch,
                self._authenticated_url, str(bare),
            )
        else:
            _git("-C", str(bare), "fetch", "--quiet", "origin", self._branch)

        worktree = Path(tempfile.mkdtemp(prefix="gitops-wt-"))
        _git(
            "-C", str(bare), "worktree", "add", "--detach", str(worktree),
            f"origin/{self._branch}",
        )
        logger.debug("cached worktree -> %s", worktree)
        return worktree

    def _resolve_manifest_path(
        self, repo_root: Path, app: str, environment: str
    ) -> Path:
        """Find the manifest file to patch.

        Resolution order:
        1. ``{app}/overlays/{env}/kustomization.yaml``
        2. ``{app}/overlays/{env}/deployment.yaml``
        3. ``{app}/{env}/deployment.yaml``
        4. ``{app}/deployment.yaml`` (single env)

        Raises ``FileNotFoundError`` when none of the conventions match.
        """
        candidates = [
            repo_root / app / "overlays" / environment / "kustomization.yaml",
            repo_root / app / "overlays" / environment / "deployment.yaml",
            repo_root / app / environment / "deployment.yaml",
            repo_root / app / "deployment.yaml",
        ]
        for candidate in candidates:
            if candidate.exists():
                logger.info("resolved manifest %s -> %s", app, candidate)
                return candidate
        raise FileNotFoundError(
            f"No manifest found for app={app!r} env={environment!r} "
            f"in {repo_root} (tried {candidates})"
        )

    def _commit_and_push(
        self, repo_path: Path, *, message: str, run_id: str
    ) -> str:
        """Stage changes, commit, push, and return the new commit SHA."""
        _git("-C", str(repo_path), "add", "--all")
        # Check if there is anything to commit (no-op promotions are idempotent).
        diff = _git(
            "-C", str(repo_path), "diff", "--cached", "--quiet",
            check=False,
        )
        if diff.returncode == 0:
            # No diff — return HEAD SHA.
            return _git("-C", str(repo_path), "rev-parse", "HEAD").strip()

        _git(
            "-C", str(repo_path), "commit", "--message", message,
            "--author", "arvino-promoter <promoter@arvino.local>",
        )
        _git("-C", str(repo_path), "push", "origin", f"HEAD:{self._branch}")

        sha = _git("-C", str(repo_path), "rev-parse", "HEAD").strip()
        logger.info("committed %s -> %s/%s", sha[:8], self._repo_url, self._branch)
        return sha


# ---------------------------------------------------------------------------
# manifest helpers
# ---------------------------------------------------------------------------


def _set_image_tag(manifest_path: Path, revision: str) -> None:
    """Set the container image tag in *manifest_path* to *revision*.

    Supports three common GitOps formats:

    * **kustomization.yaml** — ``images: [{name: ..., newTag: ...}]``.
    * **Deployment** — ``spec.template.spec.containers[0].image``.
    * **values.yaml** (Helm) — ``image.tag``.

    The file is rewritten in place.  Unrecognised formats raise a
    ``ValueError``.
    """
    raw = manifest_path.read_text()
    doc: dict[str, Any] = yaml.load(raw, Loader=_Loader)  # noqa: S506 - _Loader is SafeLoader/CSafeLoader

    if _patch_kustomization(doc, revision) or _patch_deployment(doc, revision):
        pass
    elif _patch_helm_values(doc, revision):
        pass
    else:
        raise ValueError(
            f"Cannot determine how to set image tag in {manifest_path}: "
            f"keys found: {sorted(doc.keys())}"
        )

    manifest_path.write_text(
        yaml.dump(doc, Dumper=_Dumper, default_flow_style=False, sort_keys=False)
    )


def _patch_kustomization(doc: dict[str, Any], revision: str) -> bool:
    """Update ``images`` entries in a kustomization.yaml file.

    Returns ``True`` if the document looks like a kustomization (has
    ``apiVersion: kustomize.config.k8s.io/v1beta1`` and ``kind: Kustomization``).
    """
    api = doc.get("apiVersion", "")
    kind = doc.get("kind", "")
    if "kustomize" not in str(api) or kind != "Kustomization":
        return False

    images: list[Any] = doc.setdefault("images", [])
    if not images:
        logger.warning("kustomization.yaml has no images[] — adding one")
        images.append({"name": doc.get("name", "app"), "newTag": revision})
        return True

    if isinstance(images[0], str):
        parts = images[0].rsplit(":", 1)
        images[0] = f"{parts[0]}:{revision}"
    else:
        for img in images:
            img["newTag"] = revision

    return True


def _patch_deployment(doc: dict[str, Any], revision: str) -> bool:
    """Update the first container's image tag in a Deployment.

    Returns ``True`` when ``kind`` is ``Deployment`` or ``StatefulSet``.
    """
    kind = doc.get("kind", "")
    if kind not in ("Deployment", "StatefulSet", "DaemonSet"):
        return False

    containers = (
        doc.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    if not containers:
        raise ValueError(f"{kind} has no spec.template.spec.containers")

    image = containers[0].get("image", "")
    if ":" in image:
        containers[0]["image"] = image.rsplit(":", 1)[0] + ":" + revision
    else:
        containers[0]["image"] = image + ":" + revision

    return True


def _patch_helm_values(doc: dict[str, Any], revision: str) -> bool:
    """Update ``image.tag`` in a Helm values.yaml.

    Returns ``True`` when a top-level ``image`` key with a ``tag`` sub-key exists.
    """
    image = doc.get("image")
    if not isinstance(image, dict):
        return False
    if "tag" not in image:
        return False
    image["tag"] = revision
    return True


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------


def _git(*args: str, check: bool = True) -> Any:
    """Run ``git`` and return stdout, or a CompletedProcess when check=False."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"git {' '.join(args[:3])}... failed: {stderr}")
    if not check:
        return result
    return result.stdout


def _inject_token(url: str, token: str) -> str:
    """Embed a Gitea token into the clone URL as userinfo.

    ``https://git.k8shome.com/gitea_admin/homelab.git``
    becomes ``https://arvino:{token}@git.k8shome.com/gitea_admin/homelab.git``.
    """
    if url.startswith("git@"):
        raise ValueError("SSH URLs are not supported; use the HTTPS clone URL")
    prefix = "https://"
    if not url.startswith(prefix):
        raise ValueError(f"Expected https:// clone URL, got: {url!r}")
    return f"https://arvino:{token}@{url[len(prefix):]}"