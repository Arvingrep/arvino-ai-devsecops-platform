# ADR 0005: Webhook adapters normalise to typed inbound events

Status: Accepted (2026-10-15)

## Context
Five inbound notification sources: GitHub/Gitea (issues, PRs, pushes,
statuses), CI completion, Argo CD (app health/sync), Nightingale (alerts).
Payloads differ and change.

## Decision
* Each source has a **parser** (`arvino.webhooks.*`) that authenticates the
  request, validates the payload with Pydantic, and returns a normalised
  `InboundEvent` (`IssueUpserted`, `PullRequestUpdated`, `CheckCompleted`,
  `DeploymentStatus`, `AlertReceived`). Parsers are pure functions → contract
  tests against fixtures.
* One **dispatcher** (`arvino.webhooks.dispatch`) maps events onto engine
  actions. Deduplication is by `(source, delivery_id)` in `webhook_deliveries`;
  business handlers are *also* idempotent, so replay is harmless.
* Authentication: HMAC-SHA256 over the raw body for Gitea (`X-Gitea-Signature`)
  and GitHub (`X-Hub-Signature-256`); a shared
  secret header (`X-Arvino-Token`, constant-time compare) for CI, Argo CD
  notifications and Nightingale, whose webhook features cannot sign bodies.
  A missing secret configuration means **reject** (fail closed).
* Correlation: branches created by the platform are named
  `arvino/<run_id>/<step_key>`; CI/Git events are mapped to runs via that
  branch. Argo CD applications carry the annotation `arvino.dev/run-id`
  (or are matched by `(project, environment)` for post-deploy verification).
* **Fixtures are authored from the vendors' documented payload shapes, not
  captured from live systems.** Each contract is labelled *unverified against
  live* until a real delivery has been captured (README, *Verification status*).

## Consequences
* Adding a source = one parser + fixtures + one dispatcher branch.
* Payload drift in vendor systems will show up as 422s, which are logged in
  `webhook_deliveries` with `status=rejected` for triage rather than lost.
