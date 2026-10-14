# ADR-0007: OpenHands as Alternative Agent Executor

**Status**: Accepted
**Date**: 2026-10-15

## Context

The platform shipped with two `AgentExecutor` implementations:

- `ClaudeCodeExecutor` — synchronous, shells out to the `claude -p` CLI,
  requires a local git worktree per step.
- `QueueExecutor` — a pull-model stub; steps are left in `running` status
  for an external harness to complete.

A self-hosted OpenHands Agent Canvas instance
(`https://agent-canvas.k8shome.com`, v1.54.0) is now live on the cluster.
It exposes a Conversation API that creates isolated agent sandboxes and
returns structured results — a natural fit for the `AgentExecutor` port.

The business driver is redundancy: if the Claude Code CLI is unavailable
(rate limits, version incompatibility, OAuth token rotation) a second,
independently-managed executor can pick up work without any engine changes.

## Decision

Add `OpenHandsExecutor` in `src/arvino/adapters/openhands.py` implementing
the `AgentExecutor` protocol identically to `ClaudeCodeExecutor`.

### Integration approach

**Conversation API (Option 1 from the integration doc)** was chosen over the
Automation API and the OpenAI-compatible endpoint because:

- It provides the most direct lifecycle control (create → goal → poll →
  `agent_final_response`).
- Idempotency can be achieved at the conversation level (`conv_id` stored in
  `StepOutcome.output`).
- The Automation API is designed for scheduled/webhook tasks and would require
  a separate polling harness.
- The OpenAI-compat endpoint does not expose workspace-level git state.

### Activation

`ARVINO_OPENHANDS_BASE_URL` (env, no default) activates the executor in
`build_executor()`.  When unset, the existing `ClaudeCodeExecutor` /
`QueueExecutor` selection is unchanged.  Auth is via `OPENHANDS_API_KEY` in
the host environment, never embedded in settings or logs.

### Behaviour parity with ClaudeCodeExecutor

| Concern | ClaudeCodeExecutor | OpenHandsExecutor |
|---|---|---|
| Prompt / goal construction | `_build_prompt` | `_build_goal` (identical content) |
| Artifact: note | always emitted | always emitted |
| Artifact: pull-request | extracted from JSON output | extracted from `agent_final_response` |
| Auth guard | `CLAUDE_CODE_OAUTH_TOKEN` | `OPENHANDS_API_KEY` |
| Timeout | `subprocess` timeout | wall-clock deadline + `httpx` per-request timeout |
| Retryable errors | yes | yes |
| `actor` propagation | `request.actor` | `request.actor` |

## Consequences

**Positive**

- No engine code changed; the `AgentExecutor` protocol remains the only
  coupling point.
- Operators can switch executors with a single env-var change; both can
  co-exist in different deployment stacks simultaneously.
- OpenHands provides a built-in sandboxed workspace; no local git worktree
  management is needed on the orchestrator host.

**Negative / trade-offs**

- Synchronous polling keeps the caller thread occupied for the duration of the
  agent run.  Long-running steps (> 30 min) should set `ARVINO_OPENHANDS_TIMEOUT_SECONDS`.
- The `agent_final_response` endpoint is best-effort; a 404 is treated as an
  empty response (step still succeeds if the conversation status was
  `completed`).
- No git-worktree isolation on the orchestrator side — the sandbox in OpenHands
  is the isolation boundary.  Branch-naming guardrails are enforced via the goal
  prompt, not via filesystem constraints.

## Failure mode

If the OpenHands API is unreachable, `OpenHandsExecutor.execute` returns
`StepOutcome(status="failed", retryable=True)`.  The engine's existing retry
logic handles re-dispatch without any escalation path change.
