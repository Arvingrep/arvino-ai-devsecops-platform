# ADR-0008: Replace static Bearer tokens with OIDC (Authentik)

**Status**: Accepted (2026-10-15)

## Context

`ARVINO_OWNER_TOKEN` and `ARVINO_AGENT_TOKEN` are long-lived shared secrets distributed
via Kubernetes Secrets.  They have no expiry, no per-user identity, and no audit trail
beyond "the token matched".  Rotating them requires a coordinated deploy of every consumer.

Authentik is already running on the mini (`https://auth.k8shome.com`) and issues OIDC
JWTs.  GitHub OAuth and Google OAuth were also candidates; Authentik was chosen because:

* Zero new external SaaS dependency (self-hosted, already provisioned).
* Fine-grained group-based role assignment.
* Machine-to-machine flows (Client Credentials) for agents, UI flows for humans.
* Supports the same issuer for both human and agent principals.

## Decision

### Token validation

The `Authenticator` in `api.py` inspects every `Authorization: Bearer <value>` header:

1. **JWT path** (three dot-separated segments): validate the signature against the
   issuer's JWKS, check `iss`, `aud`, and expiry.  Map a configurable claim
   (`ARVINO_OIDC_ROLE_CLAIM`, default `arvino_role`) to `PrincipalKind`:
   * claim value `owner`  → `HUMAN` + `roles=("owner",)`
   * claim value `agent`  → `AGENT`
   * claim value `system` → `SYSTEM` + `roles=("orchestrator",)`
   * missing claim or unknown value → 401

2. **Static-token path** (anything else): compare SHA-256 digest against the configured
   static tokens (`ARVINO_OWNER_TOKEN`, `ARVINO_AGENT_TOKEN`, `ARVINO_AUTOMATION_TOKEN`).
   This path is retained for:
   * CI/CD pipelines that cannot obtain OIDC tokens (e.g. Argo CD callback, n8n relay).
   * Local development (`make run` with a `.env` file).
   * The `automation_token` used by internal orchestration harness.

### Settings (new env vars)

| Variable | Default | Purpose |
|---|---|---|
| `ARVINO_OIDC_ISSUER` | `None` | OIDC issuer base URL (e.g. `https://auth.k8shome.com/application/o/arvino/`) |
| `ARVINO_OIDC_JWKS_URL` | `None` | JWKS endpoint; auto-derived from `{issuer}jwks/` when absent |
| `ARVINO_OIDC_AUDIENCE` | `None` | Expected `aud` claim; if absent, audience check is skipped |
| `ARVINO_OIDC_ROLE_CLAIM` | `arvino_role` | JWT claim name that carries the principal role |

OIDC is disabled when `ARVINO_OIDC_ISSUER` is not set.  Static tokens and OIDC can
coexist; neither is required — fail closed (both absent → 401 for all requests).

### JWKS caching

`JwksCache` fetches keys once at startup and refreshes on `kid`-not-found (rotation).
A lock guards the refresh to prevent thundering-herd on key rotation.

### Authentik configuration

* **Application**: `arvino-orchestrator` (OIDC / OAuth2 provider)
* **Human users**: assigned to group `arvino-owners`; group mapped to `arvino_role = owner`
  via a property mapping.
* **Agents / CI**: service accounts with Client Credentials; `arvino_role = agent` in
  the token via a property mapping on the provider.
* **Internal automation**: `arvino_role = system`.

## Consequences

* **Better**: tokens expire (default 10 min for CC tokens, configurable for interactive).
  Rotation is a no-op from the service's perspective (JWKS rotates automatically).
  Each principal has a real identity in Authentik audit logs.
* **Static tokens are preserved** for backward-compatibility and for sources that cannot
  use OIDC (Argo CD, n8n, Nightingale webhooks).  They SHOULD be removed once those
  integrations are migrated (tracked as P6).
* **New dependency**: `python-jose[cryptography]` (JWT decode + RSA/EC verification).
* **Deployment**: add four new optional env vars to the orchestrator Secret/ConfigMap.
  No migration; old static-token deployments continue to work unchanged.
