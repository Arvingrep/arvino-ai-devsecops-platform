# ADR 0004: OPA policy-as-code, fail-closed gates, defence in depth

Status: Accepted (2026-10-15)

## Context
Non-negotiable rules from `PROJECT.md`: agents may not merge protected
branches; production deploy / destructive migration / terraform apply /
architecture and stack changes / spending changes need owner approval; CI gates
cannot be bypassed by an LLM; QA/security cannot approve their own
implementation; no direct production writes.

## Decision
* Rules live in **Rego** under `policy/` (`arvino.authz`), with `opa test`
  unit tests. Kubernetes manifest policy for Conftest lives in `policy/k8s/`.
* The engine asks policy via the `PolicyClient` port at four decision points:
  1. `gate.evaluate` — protected paths and required checks, on **trusted
     inputs** (changed paths from the Git provider; check results from CI
     webhooks), never from agent-produced artifacts.
  2. `approval.decide` — only human principals with the `owner` role, never
     someone who acted on the run.
  3. `deploy.request` — production only via GitOps method, with an approved
     `production-deploy` approval on the same run and all required checks green.
  4. `git.write` — enforced by `GuardedGitProvider`: agents may push only
     `arvino/*` branches, never protected ones; there is no `merge` method in
     the `GitProvider` port at all.
* **Fail closed**: policy errors, timeouts, or undefined results are denials.
* **Defence in depth**: the engine independently hard-codes a *minimal* set of
  structural invariants that policy cannot relax (approval steps block
  dependents; approvals need a human; no self-approval; role-actor separation
  for review steps; production steps cannot dispatch without an approved
  approval row). Policy can only make the system stricter.
* Agent identities are role-scoped (`agent:<role>`); an executor reporting
  the same actor for an implementation step and a QA/security/review step fails
  the review step non-retryably.

## Consequences
* Changing policy is a PR to `policy/`, reviewed like code and covered by
  `opa test` in CI.
* Two layers can drift; the tests assert both layers deny the same canonical
  scenarios (`tests/test_policy_opa.py`, skipped only when `opa` is absent —
  CI always installs it).
* The `opa` CLI client is for tests/offline use; production uses the OPA HTTP
  API sidecar/Deployment.
