# ADR 0001: Platform is a thin control layer around existing tools

Status: Accepted (2026-10-15)

## Context
`PROJECT.md` describes a multi-project AI development/operations platform for a
single DevOps/Kubernetes architect. GitHub Project (portfolio/issues), Temporal (durable
execution), OpenHands/Claude Code (agent sandboxes), Gitea/GitHub (SCM + CI),
OPA/Conftest/Trivy (gates), Argo CD (deploy) and Nightingale (alerts) already
solve their own problems. Rebuilding any of them would burn a solo operator's
time and create a worse copy.

## Decision
We build exactly one service, the **orchestration service** (`arvino`). It owns
only what no existing tool owns:

1. The **workflow profile definitions** (Fast / Standard / Governed) as
   machine-readable DAGs.
2. The **run ledger**: project, issue, workflow run, step, approval, artifact,
   audit event — the cross-tool join record.
3. The **invariants** that must hold across tools (approval blocking, separation
   of duties, idempotent intake, retry/timeout fencing, fail-closed policy).
4. **Webhook adapters** that normalise Gitea/GitHub/CI/Argo CD/Nightingale
   notifications into typed events.

Everything else is reached through narrow **ports** (Python `Protocol`s):
`AgentExecutor`, `IssueTracker`, `GitProvider`, `GitOpsPromoter`,
`PolicyClient`, `RunNotifier`. Each port has a deterministic in-process fake used
by tests; production adapters are added per integration and must be labelled
"verified" only after being exercised against the real system.

## Consequences
* Small codebase (a few thousand lines) that one person can hold in their head.
* Every external dependency can be mocked, so the invariants are testable
  without any cluster, network, or credentials.
* Live adapters for OpenHands/Claude Code, Gitea and Argo CD are
  explicitly *pending* until exercised; the README tracks the status of each.
* Git remains the source of truth: artifacts store URIs + digests that point to
  Git/CI objects, not copies of chat output.
