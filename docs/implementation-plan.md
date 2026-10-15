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
2. **n8n as integration bus**: Plane/Gitea/Nightingale webhooks → n8n workflows →
   arvino API. n8n handles retry, auth, payload transform; arvino stays a pure engine.
   Replace hand-coded `IssueTracker` and notification adapters with n8n workflows.
3. Gitea/GitHub: real `GitProvider` adapter (branch, PR, changed files, check
   status); webhook delivery via n8n relay; capture real payloads into fixtures.
4. Plane: webhook → n8n → arvino upsert_issue; status writeback via n8n calling
   Plane API. No separate Python Plane adapter needed.
5. **Telegram Bot Agent Interface**: each workflow role (product, architect,
   frontend, backend, qa, security, devops, sre) maps to a Telegram bot in the
   Arvino group. `TelegramAgentExecutor` receives `StepRequest`, dispatches to the
   role's bot which runs Claude Code in an isolated worktree, and posts progress +
   results in the group. Owner approvals via `/approve` `/reject` commands.
6. OpenHands / Claude Code: `AgentExecutor` adapter launching an isolated
   worktree + sandbox per step; credentials scoped per task, no cluster-admin.
   Telegram bots (item 5) are the default frontend; headless execution is fallback.
7. Argo CD: `GitOpsPromoter` adapter that only commits to the GitOps repo
   (never calls sync on production); read-only status via n8n webhook relay.
8. Nightingale: alert webhook → n8n → arvino (create issue + trigger SRE workflow).
9. Trivy: run image/IaC scans in CI, publish results as trusted check artifacts.

## Phase 5 — BMAD artifact integration ✅
* `BmadArtifactKind` enum in `arvino.domain` (bmad-prd, bmad-architecture, bmad-story, bmad-brief).
* `arvino.bmad` module: `prd_artifact`, `architecture_artifact`, `story_artifact`, `brief_artifact`,
  `filter_bmad`, `sha256_of_text`.
* `Engine._upstream_artifacts` changed to transitive ancestor traversal so BMAD artifacts
  cross APPROVAL/CI/GATE steps without loss.
* 20 new tests in `tests/test_bmad_artifacts.py`; all 358 tests pass.
* ADR 0008 documents naming convention and design rationale.

## Phase 5 — Hardening (after live) ⬜
* ~~Alembic migrations~~ ✅ alembic initialised; initial migration (rev a4387efe1c44) covers all 10 tables; `init_schema` now runs `alembic upgrade head` instead of `create_all`; upgrade/downgrade verified against SQLite; all 309 tests green.
* Postgres row-level locking review under multiple replicas.
* External Secrets / SOPS; NetworkPolicies; mTLS to OPA.
* OIDC login instead of static bearer tokens; per-project owner mapping.
* Metrics (Prometheus) + Grafana dashboards, Nightingale rules for the platform itself.
* **BMAD v6.12**: install into each project repo; PRD/architecture/stories ingested
  as typed artifacts in the run ledger; workflow definitions reference BMAD phases
  (Clarify → Plan → Build → Verify → Learn).

## Phase 6 — Portfolio features ⬜
* Cross-project cycles/roadmap views in Plane fed by run ledger.
* Cost/budget tracking; spending-change approval kind enforced by policy.
* Self-service workflow definition PRs with automatic safety-lint.
