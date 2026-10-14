# ADR 0003: Database-authoritative workflow model; Temporal as the driver

Status: Accepted (2026-10-15)

## Context
`PROJECT.md` assigns "durable cross-project workflows, retries, timeouts, human
approval signals" to Temporal. But the *meaning* of a run — which steps exist,
which are ready, who approved what — must also be queryable, auditable and
testable without a Temporal cluster, and must survive Temporal being rebuilt.

Putting the DAG semantics inside Temporal workflow code would make the
invariants hard to unit-test (replay determinism, test server download) and
would hide the ledger in Temporal history, which is not a good system of record
for approvals and artifacts.

## Decision
1. **The relational ledger is authoritative.** Tables: `projects`, `issues`,
   `workflow_runs`, `steps`, `approvals`, `artifacts`, `events`,
   `webhook_deliveries`.
2. **The engine is a pure state-machine over the ledger** (`arvino.engine`).
   `Engine.advance(run_id)` is *idempotent and level-triggered*: it recomputes
   readiness from persisted state, claims steps with a compare-and-set
   (`UPDATE … WHERE status='pending'`), dispatches through ports, and derives
   the run status. Calling it twice, concurrently, or after a crash is safe.
3. **Workflow profiles are data** (`workflows/definitions/*.yaml`), validated
   at load: unique keys, known dependencies, acyclic, plus *safety lint*
   (production steps must descend from a required-checks gate and an owner
   approval; implementation must descend from an architecture approval in
   Governed).
4. **Temporal is a thin durable driver** (`arvino.temporal_worker`, optional
   extra): one workflow per run loops `advance_run` activity → wait for a
   `poke` signal or timer. Webhook/approval handlers update the ledger and then
   signal Temporal through the `RunNotifier` port. If Temporal is down, the
   ledger is still correct and `arvino reconcile` (or the optional in-process
   reconcile loop) advances all active runs — this is the documented recovery
   path.
5. **Fencing**: every dispatch increments `attempt`. Completions must carry the
   attempt number; stale completions (timed-out/retried attempts) are recorded
   and ignored.
6. **Retries** use per-step `max_attempts` and exponential backoff via a
   `not_before` timestamp; time comes from an injected `Clock`. **Timeouts** are
   enforced by `Engine.reap_timeouts()`.
7. **Idempotency keys**: run creation (`idempotency_key`), webhook deliveries
   (`source+delivery_id`), artifacts (`run+step+kind+name`, immutable digest),
   approval decisions (same decision twice is a no-op; conflicting is an error).

## Consequences
* All orchestration invariants are covered by fast deterministic pytest tests.
* Temporal adds durability of *driving*, not of *truth*. Losing Temporal
  history costs a re-poke, not data.
* There is deliberate duplication of "who drives the loop" (reconcile loop vs
  Temporal). It is the price of a recoverable system; both call the same
  `advance` function.
* The live Temporal path is only verified if the local dev server can be started
  (see README *Verification status*).
