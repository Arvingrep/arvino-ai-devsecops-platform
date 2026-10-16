"""HTTP API (FastAPI). Thin: auth + validation + delegation to the engine.

No ``from __future__ import annotations`` here: FastAPI resolves dependency annotations
at runtime and several are defined inside :func:`create_app`.
"""

import hashlib
import hmac
import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jose import ExpiredSignatureError, JWTError, jwt  # type: ignore[import-untyped]
from jose.exceptions import JWKError  # type: ignore[import-untyped]
from pydantic import BaseModel, Field
from sqlalchemy import text

from arvino import __version__
from arvino.db import transaction
from arvino.domain import (
    ApprovalStatus,
    ArtifactSpec,
    OrchestrationError,
    Principal,
    PrincipalKind,
    Profile,
)
from arvino.engine import Engine
from arvino.metrics import MetricsMiddleware
from arvino.metrics import collect as metrics_collect
from arvino.ports import StepOutcome
from arvino.settings import Settings, secret
from arvino.views import (
    ApprovalView,
    EventView,
    IssueView,
    ProjectView,
    RunView,
    WorkItem,
)
from arvino.webhooks import auth as wauth
from arvino.webhooks import parsers
from arvino.webhooks.dispatch import DeliveryLog, Dispatcher
from arvino.workflows.model import WorkflowDef

log = logging.getLogger(__name__)

MAX_WEBHOOK_BYTES = 1_000_000


class ProjectIn(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str
    plane_project_id: str | None = None
    repo_full_name: str | None = Field(default=None, pattern=r"^[\w.-]+/[\w.-]+$")
    default_branch: str = "main"
    argocd_apps: dict[str, str] = Field(default_factory=dict)


class IssueIn(BaseModel):
    source: str = "api"
    external_id: str
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)
    kind: Literal["task", "incident"] = "task"


class RunIn(BaseModel):
    project_id: str
    issue_id: str
    profile: Profile
    idempotency_key: str | None = None


class SuggestionIn(BaseModel):
    title: str
    body: str = ""
    phase: int | None = None


class CompleteIn(BaseModel):
    attempt: int
    status: Literal["succeeded", "failed"]
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactSpec] = Field(default_factory=list)
    error: str | None = None
    retryable: bool = True
    actor: str | None = None


class DecisionIn(BaseModel):
    decision: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=1000)


class JwksCache:
    """Fetch and cache JWKS keys; refresh on unknown ``kid``."""

    def __init__(self, jwks_url: str, _seed_keys: list[dict[str, Any]] | None = None) -> None:
        self._url = jwks_url
        self._keys: list[dict[str, Any]] = list(_seed_keys) if _seed_keys else []
        self._lock = threading.Lock()

    def _fetch(self) -> None:
        resp = httpx.get(self._url, timeout=10)
        resp.raise_for_status()
        self._keys = resp.json()["keys"]

    def keys(self, kid: str | None = None) -> list[dict[str, Any]]:
        """Return all keys, refreshing once if ``kid`` is not found."""
        if not self._keys:
            with self._lock:
                if not self._keys:
                    self._fetch()
        if kid and not any(k.get("kid") == kid for k in self._keys):
            with self._lock:
                self._fetch()
        return self._keys


class Authenticator:
    def __init__(self, settings: Settings) -> None:
        self._creds: list[tuple[str, Principal]] = []
        if t := secret(settings.owner_token):
            self._creds.append(
                (t, Principal(id=settings.owner_id, kind=PrincipalKind.HUMAN, roles=("owner",)))
            )
        if t := secret(settings.agent_token):
            self._creds.append((t, Principal(id="agent:harness", kind=PrincipalKind.AGENT)))
        if t := secret(settings.automation_token):
            self._creds.append(
                (
                    t,
                    Principal(
                        id="system:automation", kind=PrincipalKind.SYSTEM, roles=("orchestrator",)
                    ),
                )
            )

        # OIDC / JWT path
        self._oidc_issuer = settings.oidc_issuer
        self._oidc_audience = settings.oidc_audience
        self._oidc_role_claim = settings.oidc_role_claim
        self._jwks: JwksCache | None = None
        if settings.oidc_issuer:
            jwks_url = settings.oidc_jwks_url or settings.oidc_issuer.rstrip("/") + "/jwks/"
            self._jwks = JwksCache(jwks_url)

    # ---------------------------------------------------------------------- helpers

    @staticmethod
    def _looks_like_jwt(value: str) -> bool:
        parts = value.split(".")
        return len(parts) == 3 and all(parts)

    def _principal_from_jwt(self, token: str) -> Principal:
        """Validate a JWT and return the principal it represents."""
        if self._jwks is None:
            raise HTTPException(401, "OIDC not configured", headers={"WWW-Authenticate": "Bearer"})

        try:
            # Peek at the header to extract kid for targeted key lookup.
            unverified_header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise HTTPException(
                401, "malformed JWT", headers={"WWW-Authenticate": "Bearer"}
            ) from exc

        keys = self._jwks.keys(kid=unverified_header.get("kid"))

        options: dict[str, Any] = {}
        if not self._oidc_audience:
            options["verify_aud"] = False

        try:
            claims = jwt.decode(
                token,
                keys,  # python-jose accepts JWKS key list at runtime
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                audience=self._oidc_audience,
                issuer=self._oidc_issuer,
                options=options,
            )
        except ExpiredSignatureError as exc:
            raise HTTPException(
                401, "token expired", headers={"WWW-Authenticate": "Bearer"}
            ) from exc
        except (JWTError, JWKError) as exc:
            raise HTTPException(401, "invalid JWT", headers={"WWW-Authenticate": "Bearer"}) from exc

        role: str = claims.get(self._oidc_role_claim, "")
        subject: str = claims.get("sub", "oidc:unknown")

        if role == "owner":
            return Principal(id=subject, kind=PrincipalKind.HUMAN, roles=("owner",))
        if role == "agent":
            return Principal(id=subject, kind=PrincipalKind.AGENT)
        if role == "system":
            return Principal(id=subject, kind=PrincipalKind.SYSTEM, roles=("orchestrator",))

        raise HTTPException(
            401,
            f"JWT claim '{self._oidc_role_claim}' value '{role}' is not a recognised role",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def authenticate(self, header: str | None) -> Principal:
        if not header or not header.lower().startswith("bearer "):
            raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
        raw = header[7:].strip()

        if self._looks_like_jwt(raw):
            return self._principal_from_jwt(raw)

        # Static-token path: constant-time compare against all configured tokens.
        presented = hashlib.sha256(raw.encode()).digest()
        found: Principal | None = None
        for token, principal in self._creds:  # no early exit: constant work per request
            if hmac.compare_digest(hashlib.sha256(token.encode()).digest(), presented):
                found = principal
        if found is None:
            raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
        return found


def create_app(engine: Engine, settings: Settings) -> FastAPI:
    authn = Authenticator(settings)
    dispatcher = Dispatcher(engine)
    deliveries = DeliveryLog(engine._sf, engine.clock.now)
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        thread: threading.Thread | None = None
        if settings.reconcile_interval_seconds > 0:

            def loop() -> None:
                while not stop.wait(settings.reconcile_interval_seconds):
                    try:
                        engine.reconcile()
                    except Exception:
                        log.exception("reconcile loop iteration failed")

            thread = threading.Thread(target=loop, name="reconcile", daemon=True)
            thread.start()
        yield
        stop.set()
        if thread:
            thread.join(timeout=5)

    app = FastAPI(title="Arvino orchestration service", version=__version__, lifespan=lifespan)
    app.add_middleware(MetricsMiddleware)

    @app.exception_handler(OrchestrationError)
    async def _domain_error(_: Request, exc: OrchestrationError) -> JSONResponse:
        body: dict[str, Any] = {"detail": str(exc)}
        if hasattr(exc, "reasons"):
            body["reasons"] = exc.reasons
        return JSONResponse(status_code=exc.status_code, content=body)

    def current(request: Request) -> Principal:
        return authn.authenticate(request.headers.get("authorization"))

    def require(*kinds: PrincipalKind) -> Callable[..., Principal]:
        def dep(principal: Annotated[Principal, Depends(current)]) -> Principal:
            if principal.kind not in kinds:
                raise HTTPException(
                    403, f"{principal.kind.value} principals may not perform this action"
                )
            return principal

        return dep

    any_auth = Depends(current)
    operator = Depends(require(PrincipalKind.HUMAN, PrincipalKind.SYSTEM))
    worker = Depends(require(PrincipalKind.AGENT, PrincipalKind.SYSTEM, PrincipalKind.HUMAN))

    # ------------------------------------------------------------------ health

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    def readyz() -> dict[str, str]:
        try:
            with transaction(engine._sf) as s:
                s.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(503, "database unavailable") from exc
        return {"status": "ready", "adapters": settings.adapters}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(content=metrics_collect(engine._sf), media_type="text/plain")

    # ------------------------------------------------------------------ catalog

    @app.get("/workflows", dependencies=[any_auth])
    def workflows() -> list[WorkflowDef]:
        return engine.registry.all()

    @app.post("/projects", status_code=201, dependencies=[operator])
    def create_project(body: ProjectIn) -> ProjectView:
        return engine.create_project(**body.model_dump())

    @app.get("/projects", dependencies=[any_auth])
    def list_projects() -> list[ProjectView]:
        return engine.list_projects()

    @app.get("/projects/{project_id}", dependencies=[any_auth])
    def get_project(project_id: str) -> ProjectView:
        return engine.get_project(project_id)

    @app.post("/projects/{project_id}/issues", status_code=201, dependencies=[operator])
    def create_issue(project_id: str, body: IssueIn) -> IssueView:
        view, _ = engine.upsert_issue(
            project_id,
            body.source,
            body.external_id,
            body.title,
            body=body.body,
            labels=body.labels,
            kind=body.kind,
        )
        return view

    @app.get("/issues/{issue_id}", dependencies=[any_auth])
    def get_issue(issue_id: str) -> IssueView:
        return engine.get_issue(issue_id)

    @app.post("/projects/{project_id}/suggestions", status_code=201, dependencies=[operator])
    def create_suggestion(project_id: str, body: SuggestionIn) -> IssueView:
        return engine.record_suggestion(project_id, body.title, body=body.body, phase=body.phase)

    # ------------------------------------------------------------------ runs

    @app.post("/runs", status_code=201)
    def create_run(body: RunIn, principal: Annotated[Principal, operator]) -> RunView:
        key = body.idempotency_key or f"api:{body.issue_id}:{body.profile.value}"
        view, _ = engine.start_run(body.project_id, body.issue_id, body.profile, key, principal.id)
        return view

    @app.get("/runs", dependencies=[any_auth])
    def list_runs(active: bool = False, project_id: str | None = None) -> list[RunView]:
        return engine.list_runs(active_only=active, project_id=project_id)

    @app.get("/runs/{run_id}", dependencies=[any_auth])
    def get_run(run_id: str) -> RunView:
        return engine.get_run(run_id)

    @app.get("/runs/{run_id}/events", dependencies=[any_auth])
    def run_events(run_id: str) -> list[EventView]:
        engine.get_run(run_id)
        return [EventView.model_validate(e) for e in engine.events(run_id)]

    @app.post("/runs/{run_id}/advance", dependencies=[operator])
    def advance(run_id: str) -> RunView:
        return engine.advance(run_id)

    @app.post("/runs/{run_id}/cancel")
    def cancel(run_id: str, principal: Annotated[Principal, operator]) -> RunView:
        return engine.cancel_run(run_id, principal)

    @app.post("/runs/{run_id}/steps/{step_key}/complete")
    def complete_step(
        run_id: str, step_key: str, body: CompleteIn, principal: Annotated[Principal, worker]
    ) -> dict[str, str]:
        outcome = StepOutcome(
            status=body.status,
            output=body.output,
            artifacts=body.artifacts,
            error=body.error,
            retryable=body.retryable,
            actor=body.actor,
        )
        return {
            "result": engine.complete_step(run_id, step_key, body.attempt, outcome, principal).value
        }

    @app.get("/work", dependencies=[worker])
    def work(role: str | None = None) -> list[WorkItem]:
        return engine.list_work(role)

    @app.post("/admin/reconcile", dependencies=[operator])
    def reconcile() -> dict[str, list[str]]:
        return {"advanced": engine.reconcile()}

    # ------------------------------------------------------------------ approvals

    @app.get("/approvals", dependencies=[any_auth])
    def approvals(status: ApprovalStatus | None = None) -> list[ApprovalView]:
        return engine.list_approvals(status)

    @app.post("/approvals/{approval_id}/decision")
    def decide(
        approval_id: str, body: DecisionIn, principal: Annotated[Principal, any_auth]
    ) -> ApprovalView:
        # The engine (and policy) decide whether this principal may approve; agents get 403 + audit event.
        return engine.decide_approval(approval_id, principal, body.decision, body.reason)

    # ------------------------------------------------------------------ webhooks

    async def process(
        source: str,
        request: Request,
        authenticate: Callable[[bytes], None],
        parse: Callable[[bytes], list[Any]],
        delivery_header: str | None,
    ) -> dict[str, Any]:
        body = await request.body()
        if len(body) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "payload too large")
        try:
            authenticate(body)
        except wauth.WebhookAuthError as exc:
            log.warning("webhook %s rejected: %s", source, exc)
            raise HTTPException(401, "webhook authentication failed") from exc
        delivery_id = (
            request.headers.get(delivery_header) if delivery_header else None
        ) or hashlib.sha256(body).hexdigest()

        def work() -> dict[str, Any]:
            if not deliveries.begin(source, delivery_id):
                return {"status": "duplicate", "delivery_id": delivery_id}
            try:
                events = parse(body)
            except parsers.PayloadError as exc:
                deliveries.finish(source, delivery_id, "rejected", str(exc))
                raise HTTPException(422, f"invalid {source} payload: {exc}") from exc
            try:
                results = [dispatcher.handle(e) for e in events]
            except Exception as exc:
                deliveries.finish(source, delivery_id, "failed", f"{type(exc).__name__}: {exc}")
                raise
            deliveries.finish(source, delivery_id, "processed")
            return {
                "status": "processed" if events else "ignored",
                "delivery_id": delivery_id,
                "results": [r.model_dump() for r in results],
            }

        return await run_in_threadpool(work)

    def _authenticate_plane(
        authorization: str | None,
        x_plane_signature: str | None,
        plane_secret: str | None,
        owner_token: str | None,
    ) -> Callable[[bytes], None]:
        def verify(body: bytes) -> None:
            # Bearer token auth (n8n relay path)
            if authorization and authorization.startswith("Bearer "):
                token = authorization[7:]
                if owner_token and hmac.compare_digest(
                    hashlib.sha256(owner_token.encode()).digest(),
                    hashlib.sha256(token.encode()).digest(),
                ):
                    return
                raise wauth.WebhookAuthError("bearer token mismatch")
            # HMAC signature auth (direct Plane-to-arvino path)
            wauth.verify_hmac_sha256(plane_secret, body, x_plane_signature)

        return verify

    @app.post("/webhooks/plane")
    async def hook_plane(
        request: Request,
        x_plane_signature: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        return await process(
            "plane",
            request,
            lambda b: _authenticate_plane(
                authorization,
                x_plane_signature,
                secret(settings.plane_webhook_secret),
                secret(settings.owner_token),
            )(b),
            parsers.parse_plane,
            "x-plane-delivery",
        )

    @app.post("/webhooks/gitea")
    async def hook_gitea(
        request: Request,
        x_gitea_signature: Annotated[str | None, Header()] = None,
        x_gitea_event: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        return await process(
            "gitea",
            request,
            lambda b: wauth.verify_hmac_sha256(
                secret(settings.gitea_webhook_secret), b, x_gitea_signature
            ),
            lambda b: parsers.parse_git("gitea", x_gitea_event, b),
            "x-gitea-delivery",
        )

    @app.post("/webhooks/github")
    async def hook_github(
        request: Request,
        x_hub_signature_256: Annotated[str | None, Header()] = None,
        x_github_event: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        return await process(
            "github",
            request,
            lambda b: wauth.verify_hmac_sha256(
                secret(settings.github_webhook_secret), b, x_hub_signature_256, prefix="sha256="
            ),
            lambda b: parsers.parse_git("github", x_github_event, b),
            "x-github-delivery",
        )

    def token_hook(
        source: str, configured: Callable[[], str | None], parse: Callable[[bytes], list[Any]]
    ) -> Callable[..., Any]:
        async def handler(
            request: Request, x_arvino_token: Annotated[str | None, Header()] = None
        ) -> dict[str, Any]:
            return await process(
                source,
                request,
                lambda _b: wauth.verify_shared_token(configured(), x_arvino_token),
                parse,
                "x-arvino-delivery",
            )

        return handler

    app.post("/webhooks/ci")(
        token_hook("ci", lambda: secret(settings.ci_webhook_token), parsers.parse_ci)
    )
    app.post("/webhooks/argocd")(
        token_hook("argocd", lambda: secret(settings.argocd_webhook_token), parsers.parse_argocd)
    )
    app.post("/webhooks/nightingale")(
        token_hook(
            "nightingale",
            lambda: secret(settings.nightingale_webhook_token),
            parsers.parse_nightingale,
        )
    )

    # ------------------------------------------------------------------ static UI

    import pathlib

    _static_dir = pathlib.Path(__file__).resolve().parent / "static"

    def _read_html(filename: str) -> str:
        path = _static_dir / filename
        if not path.is_file():
            raise HTTPException(404, f"static file not found: {filename}")
        return path.read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/dashboard")

    @app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(_read_html("dashboard.html"))

    @app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
    def ui() -> HTMLResponse:
        return HTMLResponse(_read_html("cycle-roadmap.html"))

    return app
