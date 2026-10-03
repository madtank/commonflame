from __future__ import annotations

import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from fastapi.testclient import TestClient

from api.main import app
from app.api.v1 import oauth_as
from app.middleware.rate_limiting_middleware import rate_limit_store
from app.models.oauth_as import OAuthClient


class _NoRowsResult:
    def scalar_one_or_none(self):
        return None


class _OAuthPublicFakeDb:
    def __init__(self):
        self.added = []

    async def get(self, model, key):
        if model is OAuthClient and key == "known-public-client":
            return SimpleNamespace(
                client_id="known-public-client",
                is_active=True,
                redirect_uris=["http://127.0.0.1:6274/callback"],
                grant_types=[
                    "authorization_code",
                    "refresh_token",
                    oauth_as.DEVICE_CODE_GRANT,
                ],
                response_types=["code"],
                token_endpoint_auth_method="none",
                scope="openid offline_access ax-api/mcp:read ax-api/mcp:write",
                client_secret=None,
                client_metadata={},
            )
        return None

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        return None

    async def execute(self, statement):
        return _NoRowsResult()


async def _fake_oauth_db():
    yield _OAuthPublicFakeDb()


def _clear_rate_limit_store():
    rate_limit_store.requests.clear()
    rate_limit_store.blocked_ips.clear()
    rate_limit_store.blocked_keys.clear()
    rate_limit_store.blocked_key_reasons.clear()
    rate_limit_store.suspicious_patterns.clear()
    rate_limit_store.agent_violations.clear()
    rate_limit_store.errors_by_key.clear()


@pytest.fixture(autouse=True)
def optional_cognito_mode(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "cognito")
    for name in ("AX_AUTH_SERVER_URL", "AX_AUTH_PUBLIC_BASE_URL", "AX_AUTH_SERVER_METADATA_MODE",
                 "MCP_SERVER_URL", "AX_MCP_RESOURCE_URL", "PUBLIC_MCP_SERVER_URL",
                 "BASE_URL", "FRONTEND_URL", "AX_DEVICE_VERIFICATION_URL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def clear_rate_limits():
    _clear_rate_limit_store()
    yield
    _clear_rate_limit_store()


def _authorize_params(client_id: str = "known-public-client") -> dict[str, str]:
    return {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": "http://127.0.0.1:6274/callback",
        "scope": "openid offline_access ax-api/mcp:read",
        "resource": "http://localhost:8002/mcp",
        "code_challenge": "test-pkce-challenge",
        "code_challenge_method": "S256",
        "state": "state-123",
    }


def _decoded_cognito_state(location: str) -> dict:
    query = parse_qs(urlparse(location).query)
    state = query["state"][0]
    assert not unquote(state).startswith("https://paxai.app/oauth/authorize?")
    assert "code_verifier" not in unquote(state)
    return oauth_as._decode_cognito_login_state(state)


def test_oauth_authorize_is_public_and_redirects_unauthenticated_browser_to_login(
    monkeypatch,
):
    """Regression: /oauth/authorize must not be protected by Bearer auth.

    An MCP client opens this URL in a browser before any aX token exists. The
    public route should validate request/client metadata, then send the human to
    login/consent when no browser session token is present.
    """
    monkeypatch.setenv("AX_OAUTH_LOGIN_URL", "/auth/start")
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="http://localhost:8001")
        response = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.path == "/auth/start"
    query = parse_qs(redirect.query)
    assert "return_to" in query
    assert unquote(query["return_to"][0]).startswith(
        "http://localhost:8001/oauth/authorize?"
    )
    assert response.headers.get("www-authenticate") is None
    assert response.text != '{"detail":"Not authenticated"}'
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_oauth_authorize_defaults_to_cognito_hosted_ui_not_legacy_login(monkeypatch):
    """New MCP connections should not be sent to the archived /auth/login SPA."""
    monkeypatch.delenv("AX_OAUTH_LOGIN_URL", raising=False)
    monkeypatch.setenv(
        "VITE_COGNITO_DOMAIN", "https://ax-dev.auth.us-west-2.amazoncognito.com"
    )
    monkeypatch.setenv("COGNITO_FRONTEND_CLIENT_ID", "frontend-client")
    monkeypatch.setenv("FRONTEND_URL", "https://paxai.app")
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="https://paxai.app")
        response = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    redirect = urlparse(response.headers["location"])
    assert redirect.scheme == "https"
    assert redirect.netloc == "ax-dev.auth.us-west-2.amazoncognito.com"
    assert redirect.path == "/oauth2/authorize"
    query = parse_qs(redirect.query)
    assert query["client_id"] == ["frontend-client"]
    assert query["redirect_uri"] == ["https://paxai.app/auth/callback"]
    assert query["response_type"] == ["code"]
    assert "openid" in query["scope"][0]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"][0]
    state_payload = _decoded_cognito_state(response.headers["location"])
    assert state_payload["return_to"].startswith("https://paxai.app/oauth/authorize?")
    assert state_payload["authorize"]["client_id"] == "known-public-client"
    assert state_payload["authorize"]["state"] == "state-123"


def test_oauth_authorize_ignores_legacy_login_override_when_cognito_is_available(
    monkeypatch,
):
    """A stale prod override must not revive the archived /auth/login route."""
    monkeypatch.setenv("AX_OAUTH_LOGIN_URL", "/auth/login")
    monkeypatch.setenv("VITE_COGNITO_DOMAIN", "ax-dev.auth.us-west-2.amazoncognito.com")
    monkeypatch.setenv("COGNITO_FRONTEND_CLIENT_ID", "frontend-client")
    monkeypatch.setenv("FRONTEND_URL", "https://paxai.app")
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="https://paxai.app")
        response = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.netloc == "ax-dev.auth.us-west-2.amazoncognito.com"
    assert redirect.path == "/oauth2/authorize"


def test_oauth_authorize_infers_prod_cognito_domain_from_public_base(monkeypatch):
    """Prod uses the shared Cognito Hosted UI; no new domain var is required."""
    monkeypatch.delenv("AX_OAUTH_LOGIN_URL", raising=False)
    monkeypatch.delenv("VITE_COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("COGNITO_HOSTED_UI_DOMAIN", raising=False)
    monkeypatch.delenv("COGNITO_DOMAIN", raising=False)
    monkeypatch.setenv("COGNITO_FRONTEND_CLIENT_ID", "frontend-client")
    monkeypatch.setenv("BASE_URL", "https://paxai.app")
    monkeypatch.setenv("FRONTEND_URL", "https://paxai.app")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="https://paxai.app")
        response = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.netloc == "ax-dev.auth.us-west-2.amazoncognito.com"
    assert redirect.path == "/oauth2/authorize"


def test_oauth_authorize_infers_prod_cognito_domain_from_forwarded_host(monkeypatch):
    """CloudFront/ALB requests infer the shared Cognito domain from public host."""
    monkeypatch.delenv("AX_OAUTH_LOGIN_URL", raising=False)
    monkeypatch.delenv("AX_COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("COGNITO_HOSTED_UI_DOMAIN", raising=False)
    monkeypatch.delenv("COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("FRONTEND_COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("VITE_COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("BASE_URL", raising=False)
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    monkeypatch.setenv("COGNITO_FRONTEND_CLIENT_ID", "frontend-client")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(
            app,
            base_url="http://ax-prod-alb-721385164.us-west-2.elb.amazonaws.com",
        )
        response = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "paxai.app",
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.netloc == "ax-dev.auth.us-west-2.amazoncognito.com"
    assert redirect.path == "/oauth2/authorize"
    query = parse_qs(redirect.query)
    assert query["client_id"] == ["frontend-client"]
    assert query["redirect_uri"] == ["https://paxai.app/auth/callback"]
    state_payload = _decoded_cognito_state(response.headers["location"])
    assert state_payload["return_to"].startswith("https://paxai.app/oauth/authorize?")
    assert (
        state_payload["authorize"]["redirect_uri"] == "http://127.0.0.1:6274/callback"
    )


def test_oauth_authorize_accepts_frontend_cognito_env_aliases(monkeypatch):
    """Deploy exports FRONTEND_COGNITO_* names; backend should honor them."""
    monkeypatch.delenv("AX_OAUTH_LOGIN_URL", raising=False)
    monkeypatch.delenv("AX_COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("COGNITO_HOSTED_UI_DOMAIN", raising=False)
    monkeypatch.delenv("COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("VITE_COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("AX_COGNITO_CLIENT_ID", raising=False)
    monkeypatch.delenv("COGNITO_FRONTEND_CLIENT_ID", raising=False)
    monkeypatch.delenv("VITE_COGNITO_CLIENT_ID", raising=False)
    monkeypatch.delenv("AX_COGNITO_CALLBACK_URI", raising=False)
    monkeypatch.delenv("COGNITO_CALLBACK_URI", raising=False)
    monkeypatch.delenv("VITE_COGNITO_REDIRECT_URI", raising=False)
    monkeypatch.setenv(
        "FRONTEND_COGNITO_DOMAIN", "https://ax-prod.auth.us-west-2.amazoncognito.com"
    )
    monkeypatch.setenv("FRONTEND_COGNITO_CLIENT_ID", "frontend-client")
    monkeypatch.setenv(
        "FRONTEND_COGNITO_REDIRECT_URI", "https://paxai.app/login/callback"
    )
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="https://paxai.app")
        response = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.netloc == "ax-prod.auth.us-west-2.amazoncognito.com"
    assert redirect.path == "/oauth2/authorize"
    query = parse_qs(redirect.query)
    assert query["client_id"] == ["frontend-client"]
    assert query["redirect_uri"] == ["https://paxai.app/auth/callback"]


@pytest.mark.parametrize(
    "env_name", ["AX_COGNITO_CALLBACK_URI", "COGNITO_CALLBACK_URI"]
)
def test_oauth_authorize_ignores_spa_cognito_callback_override(monkeypatch, env_name):
    """MCP OAuth needs the backend callback; the SPA callback cannot validate it."""
    monkeypatch.delenv("AX_OAUTH_LOGIN_URL", raising=False)
    monkeypatch.setenv("VITE_COGNITO_DOMAIN", "ax-dev.auth.us-west-2.amazoncognito.com")
    monkeypatch.setenv("COGNITO_FRONTEND_CLIENT_ID", "frontend-client")
    monkeypatch.setenv("FRONTEND_URL", "https://paxai.app")
    monkeypatch.delenv("AX_COGNITO_CALLBACK_URI", raising=False)
    monkeypatch.delenv("COGNITO_CALLBACK_URI", raising=False)
    monkeypatch.setenv(env_name, "https://paxai.app/login/callback")
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="https://paxai.app")
        response = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.netloc == "ax-dev.auth.us-west-2.amazoncognito.com"
    query = parse_qs(redirect.query)
    assert query["redirect_uri"] == ["https://paxai.app/auth/callback"]


def test_oauth_cognito_callback_mints_mcp_code_and_preserves_client_state(
    monkeypatch,
):
    """Backend-owned Cognito callback completes MCP OAuth, not SPA auth."""
    monkeypatch.delenv("AX_OAUTH_LOGIN_URL", raising=False)
    monkeypatch.setenv(
        "VITE_COGNITO_DOMAIN", "https://ax-dev.auth.us-west-2.amazoncognito.com"
    )
    monkeypatch.setenv("COGNITO_FRONTEND_CLIENT_ID", "frontend-client")

    async def fake_exchange(request, *, code, code_verifier):
        assert code == "cognito-code"
        assert code_verifier
        return {"access_token": "cognito-access-token"}

    async def fake_secure_session(request, token, db):
        assert token == "cognito-access-token"
        return SimpleNamespace(user=SimpleNamespace(id=uuid.uuid4()))

    monkeypatch.setattr(oauth_as, "_exchange_cognito_authorization_code", fake_exchange)
    monkeypatch.setattr(oauth_as, "get_secure_session", fake_secure_session)
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="https://paxai.app")
        login = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            follow_redirects=False,
        )
        login_state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        response = client.get(
            "/auth/callback",
            params={"code": "cognito-code", "state": login_state},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.scheme == "http"
    assert redirect.netloc == "127.0.0.1:6274"
    assert redirect.path == "/callback"
    query = parse_qs(redirect.query)
    assert query["code"][0].startswith("code_")
    assert query["state"] == ["state-123"]


def test_oauth_cognito_callback_redirects_errors_to_mcp_client(monkeypatch):
    """Cognito denial should complete the OAuth client callback with an error."""
    monkeypatch.delenv("AX_OAUTH_LOGIN_URL", raising=False)
    monkeypatch.setenv(
        "VITE_COGNITO_DOMAIN", "https://ax-dev.auth.us-west-2.amazoncognito.com"
    )
    monkeypatch.setenv("COGNITO_FRONTEND_CLIENT_ID", "frontend-client")
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="https://paxai.app")
        login = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            follow_redirects=False,
        )
        login_state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        response = client.get(
            "/auth/callback",
            params={
                "error": "access_denied",
                "error_description": "User cancelled login",
                "state": login_state,
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.scheme == "http"
    assert redirect.netloc == "127.0.0.1:6274"
    assert redirect.path == "/callback"
    query = parse_qs(redirect.query)
    assert query["error"] == ["access_denied"]
    assert query["error_description"] == ["User cancelled login"]
    assert query["state"] == ["state-123"]


def test_oauth_cognito_callback_hands_spa_state_to_login_callback():
    """SPA-initiated Cognito logins share /auth/callback; their opaque state
    must be handed to the SPA route instead of failing Fernet validation."""
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="https://paxai.app")
        response = client.get(
            "/auth/callback",
            params={
                "code": "41efc8b2-b80b-4bec-b823-d73a13701edf",
                "state": "Bbju-fBwnTZbMOPi0cYDJzW9aTHwf77n",
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.path == "/login/callback"
    query = parse_qs(redirect.query)
    assert query["code"] == ["41efc8b2-b80b-4bec-b823-d73a13701edf"]
    assert query["state"] == ["Bbju-fBwnTZbMOPi0cYDJzW9aTHwf77n"]
    assert response.headers["cache-control"].startswith("no-store")


def test_oauth_cognito_callback_hands_spa_errors_to_login_callback():
    """Cognito error callbacks for SPA logins also belong to the SPA."""
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="https://paxai.app")
        response = client.get(
            "/auth/callback",
            params={
                "error": "access_denied",
                "error_description": "User cancelled login",
                "state": "Bbju-fBwnTZbMOPi0cYDJzW9aTHwf77n",
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.path == "/login/callback"
    query = parse_qs(redirect.query)
    assert query["error"] == ["access_denied"]
    assert query["error_description"] == ["User cancelled login"]
    assert query["state"] == ["Bbju-fBwnTZbMOPi0cYDJzW9aTHwf77n"]


def test_oauth_cognito_callback_rejects_tampered_backend_state():
    """Backend-prefixed states that fail decryption must 400, not bounce to the
    SPA — the SPA forwards gAAAA states back here, which would loop."""
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="https://paxai.app")
        response = client.get(
            "/auth/callback",
            params={"code": "cognito-code", "state": "gAAAAtampered-state-value"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid state parameter"


def test_oauth_authorize_does_not_fall_back_to_legacy_spa_when_login_unconfigured(
    monkeypatch,
):
    """Missing modern login config should fail closed instead of reviving legacy."""
    monkeypatch.setenv("AX_OAUTH_LOGIN_URL", "/auth/login")
    monkeypatch.delenv("AX_COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("COGNITO_HOSTED_UI_DOMAIN", raising=False)
    monkeypatch.delenv("COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("VITE_COGNITO_DOMAIN", raising=False)
    monkeypatch.delenv("AX_COGNITO_CLIENT_ID", raising=False)
    monkeypatch.delenv("COGNITO_FRONTEND_CLIENT_ID", raising=False)
    monkeypatch.delenv("VITE_COGNITO_CLIENT_ID", raising=False)
    monkeypatch.setenv("BASE_URL", "https://unknown.example")
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="https://unknown.example")
        response = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 500
    assert "OAuth login is not configured" in response.text
    assert "location" not in response.headers


def test_oauth_authorize_login_return_to_uses_configured_public_base(monkeypatch):
    """Regression: proxy-origin request URLs must not leak into login return_to."""
    monkeypatch.setenv("AX_OAUTH_LOGIN_URL", "/auth/start")
    monkeypatch.setenv("BASE_URL", "https://paxai.app")
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(
            app,
            base_url="http://ax-prod-alb-721385164.us-west-2.elb.amazonaws.com",
        )
        response = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.path == "/auth/start"
    query = parse_qs(redirect.query)
    return_to = query["return_to"][0]
    return_to_url = urlparse(return_to)
    assert return_to_url.scheme == "https"
    assert return_to_url.netloc == "paxai.app"
    assert return_to_url.path == "/oauth/authorize"
    assert "elb.amazonaws.com" not in return_to


def test_oauth_authorize_login_return_to_uses_api_base_when_frontend_differs(
    monkeypatch,
):
    """Docker-style env split should resume on the auth server, not frontend."""
    monkeypatch.setenv("AX_OAUTH_LOGIN_URL", "/auth/start")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    monkeypatch.setenv("BASE_URL", "http://localhost:8001")
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="http://localhost:8001")
        response = client.get(
            "/oauth/authorize",
            params=_authorize_params(),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 302
    redirect = urlparse(response.headers["location"])
    assert redirect.path == "/auth/start"
    query = parse_qs(redirect.query)
    return_to = query["return_to"][0]
    return_to_url = urlparse(return_to)
    assert return_to_url.scheme == "http"
    assert return_to_url.netloc == "localhost:8001"
    assert return_to_url.path == "/oauth/authorize"


def test_oauth_authorize_public_route_returns_invalid_client_before_login_redirect():
    """Regression: bogus public authorize requests should be OAuth errors, not 401 Bearer."""
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="http://localhost:8001")
        response = client.get(
            "/oauth/authorize",
            params=_authorize_params(client_id="missing-client"),
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_client"
    assert response.headers.get("www-authenticate") is None


def test_oauth_device_code_is_public_client_auth_only():
    """Regression: device-code issuance is pre-token and must not require Bearer auth."""
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="http://localhost:8001")
        response = client.post(
            "/oauth/device/code",
            data={
                "client_id": "known-public-client",
                "scope": "openid ax-api/mcp:read",
                "resource": "http://localhost:8002/mcp/agents/Lantern",
            },
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 200
    assert response.headers.get("www-authenticate") is None
    data = response.json()
    assert data["device_code"].startswith("dev_")
    assert data["verification_uri_complete"].startswith(
        "http://localhost:8001/device?user_code="
    )


def test_oauth_token_endpoint_is_public_and_returns_oauth_error_without_bearer():
    """Regression: token exchange authenticates OAuth clients/grants, not users via Bearer."""
    app.dependency_overrides[oauth_as.get_db_session] = _fake_oauth_db
    try:
        client = TestClient(app, base_url="http://localhost:8001")
        response = client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": "known-public-client",
                "code": "bad-code",
                "redirect_uri": "http://127.0.0.1:6274/callback",
                "code_verifier": "bad-verifier",
            },
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
    assert response.headers.get("www-authenticate") is None
