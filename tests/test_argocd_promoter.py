"""Unit tests for arvino.adapters.argocd_promoter."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from arvino.adapters.argocd_promoter import (
    _inject_token,
    _patch_deployment,
    _patch_helm_values,
    _patch_kustomization,
    _set_image_tag,
)
from arvino.ports import PromotionRequest

# ---------------------------------------------------------------------------
# _inject_token
# ---------------------------------------------------------------------------


def test_inject_token_https_url() -> None:
    result = _inject_token("https://git.k8shome.com/gitea_admin/homelab.git", "tok123")
    assert result == "https://arvino:tok123@git.k8shome.com/gitea_admin/homelab.git"


def test_inject_token_rejects_ssh() -> None:
    with pytest.raises(ValueError, match="SSH"):
        _inject_token("git@git.k8shome.com:gitea_admin/homelab.git", "tok")


def test_inject_token_rejects_non_https() -> None:
    with pytest.raises(ValueError, match="https://"):
        _inject_token("http://example.com/repo.git", "tok")


# ---------------------------------------------------------------------------
# _patch_kustomization
# ---------------------------------------------------------------------------


def test_patch_kustomization_with_existing_images() -> None:
    doc: dict = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "images": [{"name": "my-app", "newTag": "old"}],
    }
    assert _patch_kustomization(doc, "v2.0.0")
    assert doc["images"][0]["newTag"] == "v2.0.0"


def test_patch_kustomization_no_images_adds_one() -> None:
    doc: dict = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
    }
    assert _patch_kustomization(doc, "v2.0.0")
    assert doc["images"] == [{"name": "app", "newTag": "v2.0.0"}]


def test_patch_kustomization_freeform_string_images() -> None:
    doc: dict = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "images": ["registry.example.com/my-app:old"],
    }
    assert _patch_kustomization(doc, "v2.0.0")
    assert doc["images"][0] == "registry.example.com/my-app:v2.0.0"


def test_patch_kustomization_not_kustomization_returns_false() -> None:
    doc: dict = {"apiVersion": "v1", "kind": "ConfigMap"}
    assert not _patch_kustomization(doc, "v2.0.0")


# ---------------------------------------------------------------------------
# _patch_deployment
# ---------------------------------------------------------------------------


def test_patch_deployment_replaces_tag() -> None:
    doc = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"image": "reg/my-app:v1.0.0", "name": "app"},
                    ]
                }
            }
        },
    }
    assert _patch_deployment(doc, "v2.0.0")
    assert doc["spec"]["template"]["spec"]["containers"][0]["image"] == "reg/my-app:v2.0.0"


def test_patch_deployment_adds_tag() -> None:
    doc = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"image": "reg/my-app", "name": "app"},
                    ]
                }
            }
        },
    }
    assert _patch_deployment(doc, "v2.0.0")
    assert doc["spec"]["template"]["spec"]["containers"][0]["image"] == "reg/my-app:v2.0.0"


def test_patch_deployment_statefulset() -> None:
    doc = {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {"image": "reg/db:v1", "name": "db"},
                    ]
                }
            }
        },
    }
    assert _patch_deployment(doc, "v2")
    assert doc["spec"]["template"]["spec"]["containers"][0]["image"] == "reg/db:v2"


def test_patch_deployment_not_workload_returns_false() -> None:
    doc = {"apiVersion": "v1", "kind": "Service"}
    assert not _patch_deployment(doc, "v2.0.0")


def test_patch_deployment_no_containers_raises() -> None:
    doc = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "spec": {"template": {"spec": {"containers": []}}},
    }
    with pytest.raises(ValueError, match="containers"):
        _patch_deployment(doc, "v2.0.0")


# ---------------------------------------------------------------------------
# _patch_helm_values
# ---------------------------------------------------------------------------


def test_patch_helm_values() -> None:
    doc = {"image": {"repository": "reg/my-app", "tag": "old"}}
    assert _patch_helm_values(doc, "v2.0.0")
    assert doc["image"]["tag"] == "v2.0.0"


def test_patch_helm_values_no_image_returns_false() -> None:
    assert not _patch_helm_values({"replicaCount": 1}, "v2.0.0")


def test_patch_helm_values_no_tag_returns_false() -> None:
    assert not _patch_helm_values({"image": {"repository": "app"}}, "v2.0.0")


# ---------------------------------------------------------------------------
# _set_image_tag (integration of above three)
# ---------------------------------------------------------------------------


def test_set_image_tag_kustomization(tmp_path: Path) -> None:
    manifest = tmp_path / "kustomization.yaml"
    manifest.write_text(
        yaml.dump(
            {
                "apiVersion": "kustomize.config.k8s.io/v1beta1",
                "kind": "Kustomization",
                "images": [{"name": "app", "newTag": "old"}],
            }
        )
    )
    _set_image_tag(manifest, "v3.0.0")
    doc = yaml.safe_load(manifest.read_text())
    assert doc["images"][0]["newTag"] == "v3.0.0"


def test_set_image_tag_deployment(tmp_path: Path) -> None:
    manifest = tmp_path / "deployment.yaml"
    manifest.write_text(
        yaml.dump(
            {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [{"image": "reg/app:old", "name": "app"}],
                        }
                    }
                },
            }
        )
    )
    _set_image_tag(manifest, "v3.0.0")
    doc = yaml.safe_load(manifest.read_text())
    assert doc["spec"]["template"]["spec"]["containers"][0]["image"] == "reg/app:v3.0.0"


def test_set_image_tag_helm_values(tmp_path: Path) -> None:
    manifest = tmp_path / "values.yaml"
    manifest.write_text(yaml.dump({"image": {"repository": "reg/app", "tag": "old"}}))
    _set_image_tag(manifest, "v3.0.0")
    doc = yaml.safe_load(manifest.read_text())
    assert doc["image"]["tag"] == "v3.0.0"


def test_set_image_tag_unrecognised(tmp_path: Path) -> None:
    manifest = tmp_path / "unknown.yaml"
    manifest.write_text(yaml.dump({"apiVersion": "v1", "kind": "ConfigMap"}))
    with pytest.raises(ValueError, match="Cannot determine"):
        _set_image_tag(manifest, "v3.0.0")


# ---------------------------------------------------------------------------
# PromotionRequest smoke test
# ---------------------------------------------------------------------------


def test_promotion_request_model() -> None:
    req = PromotionRequest(
        run_id="run-1",
        project_key="arvino",
        environment="staging",
        revision="sha256:abc123",
        app="agent-canvas",
    )
    assert req.app == "agent-canvas"
    assert req.environment == "staging"
