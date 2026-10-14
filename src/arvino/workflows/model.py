"""Workflow profile definitions: schema, DAG validation and safety lint.

Definitions are YAML (machine-readable, reviewable) and validated at load.
Validation failures raise :class:`InvalidWorkflowError`; the service refuses to
start with an invalid definition.
"""

from __future__ import annotations

import hashlib
import json
from functools import cached_property
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from arvino.domain import (
    ENVIRONMENTS,
    IMPLEMENTER_ROLES,
    ApprovalKind,
    InvalidWorkflowError,
    Profile,
    Role,
    StepKind,
)

_DEFAULT_TIMEOUT = {
    StepKind.AGENT: 3600,
    StepKind.CI: 1800,
    StepKind.GATE: 120,
    StepKind.APPROVAL: 7 * 24 * 3600,
    StepKind.DEPLOY: 3600,
}
_DEFAULT_ATTEMPTS = {
    StepKind.AGENT: 2,
    StepKind.CI: 1,  # a failed required check fails the run; re-run is a new CI event
    StepKind.GATE: 3,  # retries only infrastructure errors; a policy denial is never retried
    StepKind.APPROVAL: 1,
    StepKind.DEPLOY: 2,
}


class StepDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    kind: StepKind
    role: Role | None = None
    depends_on: tuple[str, ...] = ()
    max_attempts: int = Field(default=0, ge=0)
    timeout_seconds: int = Field(default=0, ge=0)
    backoff_seconds: int = Field(default=30, ge=0)
    environment: str | None = None
    approval_kind: ApprovalKind | None = None
    required_checks: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_shape(self) -> StepDef:
        if self.kind is StepKind.AGENT and self.role is None:
            raise ValueError(f"step {self.key}: agent steps need a role")
        if self.kind is StepKind.APPROVAL and self.approval_kind is None:
            raise ValueError(f"step {self.key}: approval steps need approval_kind")
        if self.kind is StepKind.DEPLOY and self.environment not in ENVIRONMENTS:
            raise ValueError(f"step {self.key}: deploy steps need environment in {ENVIRONMENTS}")
        if self.kind in {StepKind.CI, StepKind.GATE} and not self.required_checks:
            raise ValueError(f"step {self.key}: {self.kind.value} steps need required_checks")
        if self.kind is not StepKind.DEPLOY and self.environment is not None:
            raise ValueError(f"step {self.key}: environment only valid on deploy steps")
        return self

    def with_defaults(self) -> StepDef:
        return self.model_copy(
            update={
                "max_attempts": self.max_attempts or _DEFAULT_ATTEMPTS[self.kind],
                "timeout_seconds": self.timeout_seconds or _DEFAULT_TIMEOUT[self.kind],
            }
        )


class WorkflowDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: Profile
    description: str
    steps: tuple[StepDef, ...]

    @cached_property
    def by_key(self) -> dict[str, StepDef]:
        return {s.key: s for s in self.steps}

    @cached_property
    def version(self) -> str:
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def ancestors(self, key: str) -> set[str]:
        seen: set[str] = set()
        stack = list(self.by_key[key].depends_on)
        while stack:
            k = stack.pop()
            if k not in seen:
                seen.add(k)
                stack.extend(self.by_key[k].depends_on)
        return seen

    def topological_order(self) -> list[str]:
        indeg = {s.key: len(s.depends_on) for s in self.steps}
        children: dict[str, list[str]] = {s.key: [] for s in self.steps}
        for s in self.steps:
            for d in s.depends_on:
                children[d].append(s.key)
        order: list[str] = []
        ready = [k for k, n in indeg.items() if n == 0]
        while ready:
            k = ready.pop(0)
            order.append(k)
            for c in children[k]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    ready.append(c)
        if len(order) != len(self.steps):
            cyc = sorted(k for k, n in indeg.items() if n > 0)
            raise InvalidWorkflowError(f"{self.profile.value}: dependency cycle among {cyc}")
        return order


def validate(defn: WorkflowDef) -> WorkflowDef:
    """Structural DAG checks plus safety lint. Returns the definition with defaults filled."""
    errors: list[str] = []
    keys = [s.key for s in defn.steps]
    if len(set(keys)) != len(keys):
        errors.append("duplicate step keys")
    known = set(keys)
    for s in defn.steps:
        for d in s.depends_on:
            if d not in known:
                errors.append(f"step {s.key}: unknown dependency {d!r}")
            if d == s.key:
                errors.append(f"step {s.key}: depends on itself")
    if errors:
        raise InvalidWorkflowError(f"{defn.profile.value}: " + "; ".join(errors))

    defn = defn.model_copy(update={"steps": tuple(s.with_defaults() for s in defn.steps)})
    defn.__dict__.pop("by_key", None)
    defn.topological_order()  # raises on cycles
    errors.extend(_safety_lint(defn))
    if errors:
        raise InvalidWorkflowError(f"{defn.profile.value}: " + "; ".join(errors))
    return defn


def _safety_lint(defn: WorkflowDef) -> list[str]:
    errs: list[str] = []
    by_key = defn.by_key

    def anc_steps(key: str) -> list[StepDef]:
        return [by_key[k] for k in defn.ancestors(key)]

    for s in defn.steps:
        if s.kind is not StepKind.DEPLOY:
            continue
        anc = anc_steps(s.key)
        if not any(a.kind is StepKind.CI for a in anc):
            errs.append(f"deploy step {s.key} must descend from a ci step")
        if not any(a.kind is StepKind.GATE for a in anc):
            errs.append(f"deploy step {s.key} must descend from a gate step")
        if s.environment == "production":
            prod_approvals = [
                a
                for a in anc
                if a.kind is StepKind.APPROVAL and a.approval_kind is ApprovalKind.PRODUCTION_DEPLOY
            ]
            if not prod_approvals:
                errs.append(
                    f"production deploy {s.key} must descend from a production-deploy approval"
                )
            if defn.profile is Profile.GOVERNED:
                if not any(a.kind is StepKind.DEPLOY and a.environment == "staging" for a in anc):
                    errs.append(f"governed production deploy {s.key} must descend from staging")
                for pa in prod_approvals:
                    if not any(
                        x.kind is StepKind.DEPLOY and x.environment == "staging"
                        for x in anc_steps(pa.key)
                    ):
                        errs.append(
                            f"governed approval {pa.key} must come after the staging deploy"
                        )
    if defn.profile is Profile.GOVERNED:
        for s in defn.steps:
            if s.kind is StepKind.AGENT and s.role in IMPLEMENTER_ROLES:
                if not any(
                    a.kind is StepKind.APPROVAL and a.approval_kind is ApprovalKind.ARCHITECTURE
                    for a in anc_steps(s.key)
                ):
                    errs.append(
                        f"governed implementation {s.key} must descend from an architecture approval"
                    )
    return errs


class WorkflowRegistry:
    def __init__(self, defs: dict[Profile, WorkflowDef]) -> None:
        missing = set(Profile) - set(defs)
        if missing:
            raise InvalidWorkflowError(
                f"missing workflow profiles: {sorted(p.value for p in missing)}"
            )
        self._defs = defs

    def get(self, profile: Profile) -> WorkflowDef:
        return self._defs[profile]

    def all(self) -> list[WorkflowDef]:
        return [self._defs[p] for p in Profile]


def parse_definition(raw: dict[str, Any]) -> WorkflowDef:
    try:
        return validate(WorkflowDef.model_validate(raw))
    except ValidationError as exc:
        raise InvalidWorkflowError(str(exc)) from exc


def load_registry(directory: Path | None = None) -> WorkflowRegistry:
    defs: dict[Profile, WorkflowDef] = {}
    if directory is None:
        files = [
            (f.name, f.read_text())
            for f in resources.files("arvino.workflows").joinpath("definitions").iterdir()
            if f.name.endswith(".yaml")
        ]
    else:
        files = [(p.name, p.read_text()) for p in sorted(directory.glob("*.yaml"))]
    for name, text in files:
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise InvalidWorkflowError(f"{name}: invalid YAML: {exc}") from exc
        wf = parse_definition(raw)
        if wf.profile in defs:
            raise InvalidWorkflowError(f"duplicate definition for profile {wf.profile.value}")
        defs[wf.profile] = wf
    return WorkflowRegistry(defs)
