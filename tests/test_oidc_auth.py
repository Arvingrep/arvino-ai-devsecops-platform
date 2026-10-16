"""OIDC authentication tests.

Exercises the JWT path in ``Authenticator`` and its integration with the
FastAPI app, without any real Authentik/OIDC server.  RSA keys are generated
in-process; the JWKS endpoint is mocked via ``httpx`` transport injection.
"""

from __future__ import annotations

import time
import unittest.mock
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwt
from pydantic import SecretStr

from arvino.api import Authenticator, JwksCache, create_app
from arvino.domain import PrincipalKind
from arvino.settings import Settings

# --------------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwk_public(rsa_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    """Public key as JWK (RS256, kid='test-key-1')."""
    from jose import jwk as jose_jwk

    pub_pem = rsa_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    d: dict[str, Any] = jose_jwk.construct(pub_pem, "RS256").to_dict()  # type: ignore[union-attr]
    d["kid"] = "test-key-1"
    d["use"] = "sig"
    return d


@pytest.fixture(scope="module")
def jwks(jwk_public: dict[str, Any]) -> dict[str, Any]:
    return {"keys": [jwk_public]}


ISSUER = "https://auth.k8shome.com/application/o/arvino/"
AUDIENCE = "arvino-api"
ROLE_CLAIM = "arvino_role"


def _make_token(
    rsa_key: rsa.RSAPrivateKey,
    role: str,
    *,
    sub: str = "user@example.com",
    expired: bool = False,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    kid: str = "test-key-1",
) -> str:
    now = int(time.time())
    exp = now - 60 if expired else now + 3600
    pem = rsa_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    claims = {
        "sub": sub,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": exp,
        ROLE_CLAIM: role,
    }
    return jwt.encode(claims, pem.decode(), algorithm="RS256", headers={"kid": kid})


def _make_settings(
    jwks_url: str = "https://auth.k8shome.com/application/o/arvino/jwks/",
) -> Settings:
    return Settings(
        database_url="sqlite://",
        oidc_issuer=ISSUER,
        oidc_jwks_url=jwks_url,
        oidc_audience=AUDIENCE,
        oidc_role_claim=ROLE_CLAIM,
        reconcile_interval_seconds=0,
    )


def _make_settings_with_static_tokens(jwks_url: str) -> Settings:
    from tests.payloads import AGENT_TOKEN, OWNER_TOKEN

    return Settings(
        database_url="sqlite://",
        owner_token=SecretStr(OWNER_TOKEN),
        agent_token=SecretStr(AGENT_TOKEN),
        oidc_issuer=ISSUER,
        oidc_jwks_url=jwks_url,
        oidc_audience=AUDIENCE,
        oidc_role_claim=ROLE_CLAIM,
        reconcile_interval_seconds=0,
    )


# --------------------------------------------------------------------------- unit: Authenticator


def test_looks_like_jwt_helper() -> None:
    assert Authenticator._looks_like_jwt("a.b.c")
    assert not Authenticator._looks_like_jwt("notajwt")
    assert not Authenticator._looks_like_jwt("a.b")
    assert not Authenticator._looks_like_jwt("a.b.c.d")
    assert not Authenticator._looks_like_jwt("")
    assert not Authenticator._looks_like_jwt(".")
    assert not Authenticator._looks_like_jwt("..")
    assert not Authenticator._looks_like_jwt("a..c")


def test_static_token_path_unchanged(rsa_key: rsa.RSAPrivateKey, jwks: dict[str, Any]) -> None:
    """Static tokens still work even when OIDC is configured."""
    import httpx as _httpx

    from tests.payloads import AGENT_TOKEN, OWNER_TOKEN

    jwks_url = "https://auth.k8shome.com/application/o/arvino/jwks/"
    settings = _make_settings_with_static_tokens(jwks_url)

    # Mock the JWKS endpoint so JwksCache doesn't hit the network.
    class _FakeTransport(_httpx.BaseTransport):
        def handle_request(self, request: _httpx.Request) -> _httpx.Response:
            return _httpx.Response(200, json=jwks)

    import arvino.api as api_mod

    def _fake_get(url: str, **kw: Any) -> _httpx.Response:
        return _httpx.Client(transport=_FakeTransport()).get(url, **kw)

    import unittest.mock as mock

    with mock.patch.object(api_mod.httpx, "get", side_effect=_fake_get):
        authn = Authenticator(settings)
        owner = authn.authenticate(f"Bearer {OWNER_TOKEN}")
        assert owner.kind == PrincipalKind.HUMAN and "owner" in owner.roles
        agent = authn.authenticate(f"Bearer {AGENT_TOKEN}")
        assert agent.kind == PrincipalKind.AGENT


# --------------------------------------------------------------------------- integration: FastAPI client


@pytest.fixture
def oidc_client(rsa_key: rsa.RSAPrivateKey, jwks: dict[str, Any], env: Any) -> TestClient:
    """TestClient with OIDC Authenticator pre-loaded with test JWKS keys (no network)."""
    import arvino.api as api_mod

    settings = _make_settings()
    # Pre-seed the JwksCache with our test keys so no HTTP fetch occurs.
    jwks_url = settings.oidc_issuer.rstrip("/") + "/jwks/"  # type: ignore[union-attr]
    seed_cache = api_mod.JwksCache(jwks_url, _seed_keys=jwks["keys"])

    with unittest.mock.patch.object(api_mod, "JwksCache", return_value=seed_cache):
        app = create_app(env.engine, settings)

    return TestClient(app)


def _jwt_header(rsa_key: rsa.RSAPrivateKey, role: str, **kw: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_token(rsa_key, role, **kw)}"}


def test_oidc_owner_token_can_list_projects(
    oidc_client: TestClient, rsa_key: rsa.RSAPrivateKey
) -> None:
    h = _jwt_header(rsa_key, "owner")
    r = oidc_client.get("/projects", headers=h)
    assert r.status_code == 200


def test_oidc_agent_token_can_list_work(
    oidc_client: TestClient, rsa_key: rsa.RSAPrivateKey
) -> None:
    h = _jwt_header(rsa_key, "agent")
    r = oidc_client.get("/work", headers=h)
    assert r.status_code == 200


def test_oidc_system_token_acts_as_operator(
    oidc_client: TestClient, rsa_key: rsa.RSAPrivateKey
) -> None:
    h = _jwt_header(rsa_key, "system")
    r = oidc_client.post("/projects", json={"key": "sys-proj", "name": "Sys"}, headers=h)
    assert r.status_code == 201


def test_oidc_agent_cannot_create_project(
    oidc_client: TestClient, rsa_key: rsa.RSAPrivateKey
) -> None:
    h = _jwt_header(rsa_key, "agent")
    r = oidc_client.post("/projects", json={"key": "agent-proj", "name": "A"}, headers=h)
    assert r.status_code == 403


def test_oidc_expired_token_is_401(oidc_client: TestClient, rsa_key: rsa.RSAPrivateKey) -> None:
    h = _jwt_header(rsa_key, "owner", expired=True)
    r = oidc_client.get("/projects", headers=h)
    assert r.status_code == 401


def test_oidc_unknown_role_is_401(oidc_client: TestClient, rsa_key: rsa.RSAPrivateKey) -> None:
    h = _jwt_header(rsa_key, "villain")
    r = oidc_client.get("/projects", headers=h)
    assert r.status_code == 401


def test_oidc_wrong_issuer_is_401(oidc_client: TestClient, rsa_key: rsa.RSAPrivateKey) -> None:
    h = _jwt_header(rsa_key, "owner", issuer="https://evil.example.com/")
    r = oidc_client.get("/projects", headers=h)
    assert r.status_code == 401


def test_oidc_wrong_audience_is_401(oidc_client: TestClient, rsa_key: rsa.RSAPrivateKey) -> None:
    h = _jwt_header(rsa_key, "owner", audience="wrong-audience")
    r = oidc_client.get("/projects", headers=h)
    assert r.status_code == 401


def test_malformed_jwt_is_401(oidc_client: TestClient) -> None:
    r = oidc_client.get("/projects", headers={"Authorization": "Bearer a.b.c"})
    assert r.status_code == 401


def test_missing_token_is_401(oidc_client: TestClient) -> None:
    r = oidc_client.get("/projects")
    assert r.status_code == 401


def test_jwt_without_oidc_configured_is_401(env: Any) -> None:
    """A JWT presented to a server with no OIDC configured must be rejected."""
    settings = Settings(
        database_url="sqlite://",
        reconcile_interval_seconds=0,
        # No oidc_issuer, no static tokens.
    )
    app = create_app(env.engine, settings)
    c = TestClient(app)
    # Build a plausible-looking token with a fake key — doesn't matter, should 401 immediately.
    fake_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ4In0.fake"
    r = c.get("/projects", headers={"Authorization": f"Bearer {fake_token}"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- unit: JwksCache


def test_jwks_cache_fetches_on_first_call(jwks: dict[str, Any]) -> None:
    import unittest.mock as mock

    cache = JwksCache("https://example.com/jwks/")
    with mock.patch("arvino.api.httpx.get") as m:
        m.return_value.json.return_value = jwks
        m.return_value.raise_for_status = lambda: None
        keys = cache.keys()
    assert len(keys) == 1
    m.assert_called_once()


def test_jwks_cache_refreshes_on_unknown_kid(jwks: dict[str, Any]) -> None:
    import unittest.mock as mock

    cache = JwksCache("https://example.com/jwks/")
    with mock.patch("arvino.api.httpx.get") as m:
        m.return_value.json.return_value = jwks
        m.return_value.raise_for_status = lambda: None
        # First call populates the cache.
        cache.keys()
        # Second call with a known kid — no re-fetch.
        cache.keys(kid="test-key-1")
        assert m.call_count == 1
        # Third call with unknown kid — triggers a refresh.
        cache.keys(kid="unknown-key")
        assert m.call_count == 2
