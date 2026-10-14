# ADR 0002: Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2

Status: Accepted (2026-10-15)

## Context
Requirements: typed, testable, small, runs locally without a cluster, talks
HTTP to many tools, and can host a Temporal worker (Temporal has first-class
Python and Go SDKs). The operator is a DevOps/Kubernetes architect, comfortable
with Go and Python. Candidates considered:

| Option | For | Against |
|---|---|---|
| **Python + FastAPI + Pydantic + SQLAlchemy** | Pydantic gives typed boundary validation for webhooks and YAML workflow definitions; pytest + `Protocol` fakes make invariants cheap to test; Temporal Python SDK; OPA/Rego tooling is language-neutral | Needs mypy discipline; GIL irrelevant at this scale |
| Go + chi + sqlc | Single static binary, great K8s fit, Temporal Go SDK | More boilerplate for webhook/YAML schemas; slower iteration on a state-machine whose shape is still moving |
| TypeScript + Fastify + Zod | Typed, fast to write | Third toolchain for the operator; weaker Temporal story than Go/Python |

## Decision
* **Python 3.12** (`requires-python >=3.11`), **FastAPI** for the HTTP API,
  **Pydantic v2** for all boundary and domain value objects, **SQLAlchemy 2.0**
  (sync) for persistence, **httpx** for outbound HTTP, **PyYAML** for workflow
  definitions.
* **SQLite** for unit/integration tests and zero-dependency local runs;
  **PostgreSQL 16** in Compose and Kubernetes. The same SQLAlchemy models run on
  both; the test-suite runs on SQLite and the Compose smoke test on Postgres.
* Quality gates: `ruff` (lint + security `S` rules + import order), `mypy
  --strict`, `pytest`, `yamllint`, `opa test`, `kubectl kustomize` + `kubeconform`,
  `gitleaks`.
* Sync SQLAlchemy and sync FastAPI handlers on purpose: the engine is a short,
  transactional state machine; async adds failure modes without throughput we
  need. Long waits live in Temporal / external systems, not in request handlers.
* Schema is created by `Base.metadata.create_all` for the MVP. Alembic is
  deferred (Phase 2) — this is a recorded limitation, not an oversight.

## Consequences
* One language for service, tests and the Temporal worker.
* Strict typing is enforced in CI (`mypy --strict`), compensating for Python's
  dynamism.
* Switching the DB to Postgres-only later is straightforward; we avoid
  SQLite-specific SQL so both stay supported.
