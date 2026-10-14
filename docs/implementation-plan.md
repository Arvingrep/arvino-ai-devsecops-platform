# Phased implementation plan

Status key: ✅ done & verified locally · 🟡 done, contract/mock only (live step pending) · ⬜ not started

## Phase 0 — Foundations (tonight) ✅
* ADRs 0001–0006, this plan.
* Repo tooling: pyproject, ruff, mypy strict, pytest, yamllint, gitleaks config.

## Phase 1 — Orchestration core (tonight) ✅
* Domain model + SQL ledger (project, issue, run, step, approval, artifact, event).
* Workflow definitions (Fast/Standard/Governed) as YAML + DAG validation + safety lint.
* Engine: ready-set computation, parallel fan-out/fan-in, CAS claiming, retries with
  backoff, timeouts, attempt fencing, approval blocking, separation of duties.
* REST API + token auth; CLI (`arvino reconcile`, `arvino validate-workflows`).
* Tests for all of the above.

## Phase 2 — Policy & webhooks (tonight) ✅ / 🟡
* Rego policy (`policy/`) with `opa test`; OPA CLI + HTTP clients; fail-closed.
* Webhook parsers + dispatcher for Plane, Gitea/GitHub, CI, Argo CD, Nightingale
  (🟡 fixtures authored from docs; live capture pending).
* Conftest policy for Kubernetes manifests.

## Phase 3 — Delivery packaging (tonight) ✅ / 🟡
* Dockerfile, `compose.yaml` (Postgres + OPA + service) — verified locally if Docker allowed.
* Kustomize bases/overlays for orchestrator, Plane, Temporal, OPA; Argo CD
  AppProject/Applications. Static validation only (🟡 never applied).
* CI workflow (Gitea Actions / GitHub Actions compatible).

## Phase 4 — Live integrations (next) ⬜
Each item ends with a recorded real-system verification:
1. Temporal: deploy server, run `arvino.temporal_worker`, kill/restart worker mid-run.
2. Gitea/GitHub: real `GitProvider` adapter (branch, PR, changed files, check
   status); capture real webhook deliveries into fixtures.
3. Plane: real `IssueTracker` adapter (state/comment writeback) and webhook capture.
4. OpenHands / Claude Code: `AgentExecutor` adapter launching an isolated
   worktree + sandbox per step; credentials scoped per task, no cluster-admin.
5. Argo CD: `GitOpsPromoter` adapter that only commits to the GitOps repo
   (never calls sync on production); read-only status via notifications.
6. Nightingale: capture the real alert webhook template and align the contract.
7. Trivy: run image/IaC scans in CI, publish results as trusted check artifacts.

## Phase 5 — Hardening (after live) ⬜
* Alembic migrations; Postgres row-level locking review under multiple replicas.
* External Secrets / SOPS; NetworkPolicies; mTLS to OPA.
* OIDC login instead of static bearer tokens; per-project owner mapping.
* Metrics (Prometheus) + Grafana dashboards, Nightingale rules for the platform itself.
* BMAD v6.12 artifact ingestion (PRD/architecture/stories) as typed artifacts.

## Phase 6 — Portfolio features ⬜
* Cross-project cycles/roadmap views in Plane fed by run ledger.
* Cost/budget tracking; spending-change approval kind enforced by policy.
* Self-service workflow definition PRs with automatic safety-lint.
