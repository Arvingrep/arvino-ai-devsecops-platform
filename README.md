# Arvino AI DevSecOps Platform

Multi-project agile AI development, operations, and maintenance platform for a solo DevOps/Kubernetes architect.

**Not another AI company wrapper.** This is a thin orchestration layer that coordinates Plane, Temporal, OpenHands/Claude Code, Gitea/GitHub, OPA, Trivy, Argo CD and Nightingale — it only owns what no existing tool owns: workflow DAGs, the cross-tool run ledger, and the invariants between them.

## Architecture

```
Plane / Gitea / Nightingale  →  Webhooks  →  arvino (FastAPI)
                                                 │
                            ┌────────────────────┤
                            ▼                    ▼
                    Engine (state machine)    OPA (policy)
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
         AgentExecutor   GitProvider   GitOpsPromoter
        (OpenHands/CC)   (Gitea/GH)    (Argo CD)
                            │
                         Temporal
                      (durable driver)
```

## Quick start

```bash
git clone https://github.com/Arvingrep/arvino-ai-devsecops-platform.git
cd arvino-ai-devsecops-platform
uv sync --all-extras   # or: pip install -e '.[temporal,dev]'

# Run tests (130 tests, ~8s)
pytest -q

# Full local CI
scripts/check.sh --fast   # skip network fetches
scripts/check.sh          # full (needs helm, kubeconform, conftest, opa, gitleaks)

# Validate workflow definitions
arvino validate-workflows

# Docker Compose (PostgreSQL + OPA + arvino API)
cp .env.example .env
docker compose up -d
curl http://localhost:8000/healthz
```

## Workflow profiles

| Profile | When | Flow | Approvals |
|---|---|---|---|
| **Fast** | Bug, config tweak | triage → develop → test → PR → CI → gate → preview | 0 (gate blocks protected paths) |
| **Standard** | Feature | spec → arch → frontend+backend → CI → QA+security → review → gate → preview → **approval** → production | 1 |
| **Governed** | Arch, infra, migration | research → spec → ADR → **approval** → impl → CI → QA+security+IaC → review → gate → staging → **approval** → production → verify | 2 |

## Non-negotiable rules

- **Git is truth.** Artifacts are URIs + digests pointing to Git objects.
- **No merge method.** `GitProvider` has no `merge()` — no code path can merge a PR.
- **Separation of duties.** Implementers ≠ reviewers within the same run.
- **Approvals are human-only.** `human` + `owner` role + didn't participate.
- **Policy fails closed.** OPA errors = denial.
- **Agent merge = run failure.** Webhook reports agent merged → run fails.
- **Production requires owner approval.** Engine invariant, not just policy.

## Verification status

| Component | Status | Evidence |
|---|---|---|
| Engine invariants | ✅ | 130 pytest, mypy strict, ruff clean |
| OPA policy | ✅ | Rego + Python tests |
| Kustomize manifests | ✅ | 3 overlays build clean |
| Conftest K8s policies | ✅ | Positive + negative controls |
| Docker Compose | ✅ | `docker compose config` valid |
| Webhook parsers | 🟡 | Contract tests (not live capture) |
| Temporal driver | 🟡 | Unit test passes; no live server |
| Live adapters | ⬜ | Ports defined, fakes implemented |
| GKE deployment | ⬜ | Manifests generated, not applied |

## Recovery

```bash
arvino reconcile   # reap timeouts + re-advance all active runs
# Engine is level-triggered: advance() is safe to call any number of times.
# Without Temporal, cron `arvino reconcile` every 60s is sufficient.
```

## Known limitations (MVP)

- Schema via `create_all`, no Alembic (Phase 5)
- Static bearer token auth, no OIDC (Phase 5)
- Live adapters pending (Phase 4)
- Single-replica assumed

## Next: [docs/implementation-plan.md](docs/implementation-plan.md)

## License

MIT
