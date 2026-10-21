"""Routes a StepRequest.role to the correct AgentExecutor adapter.

Design decision (set by orchestrator): single registry, fail-closed.
"""

from __future__ import annotations

from arvino.domain import Role
from arvino.ports import AgentExecutor, StepOutcome, StepRequest

# Every workflow role must be routable: an unmapped role fails the run
# immediately ("no executor configured").  Cover the full Role enum so the
# built-in fast/standard/governed definitions cannot drift out of sync again.
# Extra ten-team role names are kept for custom workflows.
_ROLE_MAP: dict[str, str] = {role.value: "claude_code" for role in Role}
_ROLE_MAP.update(
    {
        "devops": "claude_code",
        "tester": "claude_code",
        "design": "claude_code",
        "smartcontract": "claude_code",
        "lead": "claude_code",
    }
)


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
