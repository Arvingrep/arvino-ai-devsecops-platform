# ADR 0006: Kustomize GitOps layout; placeholder-only secrets

Status: Accepted (2026-10-15)

## Context
Argo CD is the deployer; the repo must contain manifests for Temporal and
the orchestration service, but no cluster is touched tonight and no secret may be
committed.

## Decision
* `deploy/base/<component>` + `deploy/overlays/{dev,staging,production}` with
  Kustomize (validated offline by `kubectl kustomize` and `kubeconform`).
  Helm is not used for our own service; Temporal is deployed from
  minimal Deployments/StatefulSets here for MVP reproducibility, with the
  upstream Helm charts named as the Phase 3 replacement.
* `deploy/argocd/` holds `AppProject` + `Application`/`ApplicationSet`
  manifests. Production `Application` has **no automated sync**; dev/staging
  may auto-sync. The AppProject denies cluster-scoped resources except a named
  allowlist.
* Secrets: `*.example.yaml` Secret manifests with obviously fake values
  (`REPLACE_ME_…`) are excluded from the Kustomize bases. Real secrets come from
  External Secrets/SOPS (Phase 3); `gitleaks` + a Conftest rule (`deny Secret with
  non-placeholder data`) enforce this in CI.
* Containers: non-root, read-only root FS, dropped capabilities, resource limits,
  pinned image tags (never `:latest`); enforced by Conftest policy.
* Docker Compose (`compose.yaml`) is the local verification stack: Postgres,
  OPA, the orchestration service. Temporal in Compose is behind the
  optional `full` profile (large images; not exercised in CI).

## Consequences
* Production changes require an owner-reviewed PR to `deploy/overlays/production`
  and a manual Argo CD sync, matching the "no direct production writes" rule.
* Nothing in this repo has been applied to a cluster; manifests are only
  statically validated until Phase 3.
