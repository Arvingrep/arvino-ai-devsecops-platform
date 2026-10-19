"""Routes a StepRequest.role to the correct AgentExecutor adapter.

Design decision (set by orchestrator): single registry, fail-closed.
"""

from __future__ import annotations

from arvino.ports import AgentExecutor, StepOutcome, StepRequest

_ROLE_MAP: dict[str, str] = {
    # Role value -> adapter key
    "backend": "claude_code",
    "architect": "claude_code",
    "qa": "claude_code",
    "security": "claude_code",
    "reviewer": "claude_code",
    "devops": "claude_code",
    "frontend": "claude_code",
    "researcher": "claude_code",
}


class RoleRouter:
    """Selects the correct AgentExecutor for a given role. Fail-closed: unknown roles raise."""

    def __init__(self, adapters: dict[str, AgentExecutor]) -> None:
        self._adapters = adapters

    @property
    def adapters(self) -> dict[str, AgentExecutor]:
        return dict(self._adapters)

    def execute(self, request: StepRequest) -> StepOutcome:
        adapter_key = _ROLE_MAP.get(request.role)
        if adapter_key is None:
            return StepOutcome(
                status="failed",
                error=f"no executor configured for role {request.role!r}",
                retryable=False,
            )
        adapter = self._adapters.get(adapter_key)
        if adapter is None:
            return StepOutcome(
                status="failed",
                error=f"executor {adapter_key!r} not available",
                retryable=True,
            )
        return adapter.execute(request)
