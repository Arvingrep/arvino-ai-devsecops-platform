# Architecture Decision Records

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-thin-control-layer.md) | Platform is a thin control layer around existing tools | Accepted |
| [0002](0002-implementation-stack.md) | Python 3.12 + FastAPI + Pydantic + SQLAlchemy | Accepted |
| [0003](0003-durable-workflow-model.md) | Database-authoritative workflow model; Temporal as driver | Accepted |
| [0004](0004-policy-as-code-and-gates.md) | OPA policy-as-code, fail-closed gates, defence in depth | Accepted |
| [0005](0005-webhook-contracts.md) | Webhook adapters normalise to typed inbound events | Accepted |
| [0006](0006-gitops-deployment-and-secrets.md) | Kustomize GitOps layout; placeholder-only secrets | Accepted |
| [0008](0008-oidc-authentication.md) | Replace static Bearer tokens with OIDC (Authentik) | Accepted |

Format: context, decision, consequences, status. Superseding an ADR requires a new
ADR; do not edit accepted decisions in place except to fix typos.
