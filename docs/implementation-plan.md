# Phased implementation plan

Status key: ✅ done & verified · 🟡 done, pending live verification · ⬜ not started

## Phase 0 — Foundations ✅
* ADRs 0001–0008, this plan.
* Repo tooling: pyproject, ruff, mypy strict, pytest, yamllint, gitleaks config.

## Phase 1 — Orchestration core ✅
* Domain model + SQL ledger (project, issue, run, step, approval, artifact, event).
* Workflow definitions (Fast/Standard/Governed) as YAML + DAG validation + safety lint.
* Engine: ready-set computation, parallel fan-out/fan-in, CAS claiming, retries with
  backoff, timeouts, attempt fencing, approval blocking, separation of duties.
* REST API + token auth; CLI (`arvino reconcile`, `arvino validate-workflows`).
* 419 tests passing.

## Phase 2 — Policy & webhooks ✅
* Rego policy (`policy/`) with `opa test`; OPA CLI + HTTP clients; fail-closed.
* Webhook parsers + dispatcher for Gitea/GitHub, CI, Argo CD, Nightingale.
* Real webhook fixtures captured from live systems.
* Conftest policy for Kubernetes manifests (positive + negative controls).

## Phase 3 — Delivery packaging ✅
* Dockerfile, `compose.yaml` (Postgres + OPA + service).
* Kustomize bases/overlays for orchestrator, Temporal, OPA.
* Argo CD AppProject/Applications — deployed to GKE.
* CI workflow (GitHub Actions: lint + test + policy + build + push via OIDC WIF).
* Trivy image + IaC scan integrated in CI.

## Phase 4 — Live integrations ✅
1. ✅ Temporal: deployed to GKE, worker connected, kill/restart verification.
2. ✅ **n8n as integration bus**: 3 webhook relay workflows active (Gitea/GitHub/Nightingale → arvino).
3. ✅ Gitea/GitHub: GitHubProvider + GiteaProvider adapters (17 tests). GitHub webhook on firebird-sea active.
4. ✅ GitHub: issue tracking on firebird-sea active, webhook → arvino verified.
5. ✅ **Telegram Bot Agent Interface**: TelegramAgentExecutor adapter built. 10 bots verified in group. Owner /approve /reject tested.
6. ✅ OpenHands / Claude Code: ClaudeCodeExecutor (22 tests) + OpenHandsExecutor (ADR 0007). Agent harness script for local execution.
7. ✅ Argo CD: ArgoCDPromoter adapter (commits to GitOps repo only, never syncs).
8. ✅ Nightingale: alert webhook → n8n → arvino Issue creation verified.
9. ✅ Trivy: image + IaC scans in CI.
* ✅ E2E verified: firebird-sea Issue #67 → Fast workflow → PR #114 created by Agent.
* ✅ Multi-project: arvino-ai-devsecops-platform registered as second project.

## Phase 5 — Hardening ✅
* ✅ Alembic migrations (rev a4387efe1c44, 10 tables).
* ✅ External Secrets / SOPS for declarative secret management.
* ✅ NetworkPolicies isolating arvino namespace.
* ✅ OIDC login replacing static bearer tokens.
* ✅ Prometheus metrics + Grafana dashboards + Nightingale rules for the platform.
* ✅ **BMAD v6.12**: installed in firebird-sea (PR #113 merged), artifacts integrated
  into run ledger (BmadArtifactKind enum, transitive ancestor traversal, 20 tests).

## Phase 6 — Portfolio features ✅
* ✅ Cross-project roadmap views in GitHub Project fed by run ledger.
* ✅ Cost/budget tracking; spending-change approval kind enforced by policy.
* ✅ Self-service workflow definition PRs with automatic safety-lint.
* ✅ Director logic programmatized into arvino engine (RoleRouter, ReconcileDriver,
  TelegramRunNotifier, suggestion intake API).
* ✅ Codexhost multi-agent evaluation completed.
