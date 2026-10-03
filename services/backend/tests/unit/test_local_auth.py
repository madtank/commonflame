from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
import pytest
from starlette.requests import Request

from app.api.v1 import local_auth
from app.core import ax_jwt
from app.core.rls import get_system_session
from app.middleware.rate_limiting_middleware import RateLimitingMiddleware


@pytest.fixture
def session(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    user = SimpleNamespace(id=uuid.uuid4(), space_id=uuid.uuid4(), current_space_id=None,
                           username="tester", email="tester@waystation.local", full_name="Test User",
                           active=True, role="user", token_version=0, auth_provider="local",
                           password_hash=local_auth.password_hasher.hash("correct-test-password"))
    result = SimpleNamespace(scalar_one_or_none=lambda: user)
    db = SimpleNamespace(execute=AsyncMock(return_value=result), get=AsyncMock(return_value=user),
                         add=lambda _: None, commit=AsyncMock())
    system = SimpleNamespace(db=db)
    app = FastAPI()
    app.include_router(local_auth.router)
    app.dependency_overrides[get_system_session] = lambda: system
    return TestClient(app), db, user


def test_password_login_has_short_lived_signed_tokens_and_private_refresh_cookie(session):
    client, _, user = session
    response = client.post("/auth/local/login", json={"username": "tester", "password": "correct-test-password"})
    assert response.status_code == 200
    body = response.json()
    _, public_key = ax_jwt._get_signing_key()
    claims = jwt.decode(body["access_token"], public_key, algorithms=["RS256"], audience="ax-api", issuer="ax-backend")
    assert claims["typ"] == "local-user"
    assert claims["exp"] - claims["iat"] == 900
    assert claims["user_id"] == str(user.id)
    assert "http://localhost:3000/mcp" in claims["aud"]
    assert "refresh_token" not in body
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/auth/local" in cookie
    assert "Domain=" not in cookie
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("password,active,provider", [
    ("wrong-password", True, "local"), ("correct-test-password", False, "local"),
    ("correct-test-password", True, "github"),
])
def test_password_login_rejects_wrong_password_inactive_and_nonlocal_accounts(session, password, active, provider):
    client, db, user = session
    user.active, user.auth_provider = active, provider
    response = client.post("/auth/local/login", json={"username": "tester", "password": password})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"
    db.commit.assert_not_awaited()


def test_cookie_actions_reject_cross_site_origin(session):
    client, db, _ = session
    response = client.post("/auth/local/login", headers={"Origin": "https://attacker.example"},
                           json={"username": "tester", "password": "correct-test-password"})
    assert response.status_code == 403
    db.execute.assert_not_awaited()


def test_refresh_rotates_and_old_cookie_cannot_replay(session):
    client, db, user = session
    row = SimpleNamespace(user_id=user.id, revoked_at=None,
                          expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: row)
    client.cookies.set(local_auth.COOKIE_NAME, "old-test-refresh")
    response = client.post("/auth/local/refresh")
    assert response.status_code == 200
    assert row.revoked_at is not None
    assert "old-test-refresh" not in response.headers["set-cookie"]
    client.cookies.set(local_auth.COOKIE_NAME, "old-test-refresh")
    assert client.post("/auth/local/refresh").status_code == 401


def test_login_rate_limit_cannot_be_evaded_with_forged_agent_or_proxy_headers():
    middleware = RateLimitingMiddleware(lambda: None)
    request = Request({"type": "http", "method": "POST", "path": "/auth/local/login",
                       "headers": [(b"x-agent-name", b"forged-agent"), (b"x-forwarded-for", b"198.51.100.9")],
                       "client": ("127.0.0.1", 1234), "query_string": b""})
    assert middleware._get_client_ip(request) == "127.0.0.1"
    assert middleware._get_rate_limit_config("/auth/local/login") == {"requests": 5, "window": 60, "burst": 2}


def test_space_switch_tokens_keep_the_mcp_verifiable_browser_session(session):
    from app.api.v1.spaces import create_access_token
    _, _, user = session
    space_id = str(uuid.uuid4())
    token = create_access_token(str(user.id), space_id, 0,
                                extra_claims={"username": user.username, "email": user.email})
    _, public_key = ax_jwt._get_signing_key()
    claims = jwt.decode(token, public_key, algorithms=["RS256"], audience="ax-api", issuer="ax-backend")
    assert claims["typ"] == "local-user"
    assert claims["space_id"] == space_id
    assert "http://localhost:3000/mcp" in claims["aud"]


def test_signing_key_file_preserves_jwks_across_reload(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "signing.pem"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    monkeypatch.setenv("AX_JWT_PRIVATE_KEY_FILE", str(path))
    ax_jwt._get_signing_key.cache_clear()
    first = ax_jwt.get_jwks()
    ax_jwt._get_signing_key.cache_clear()
    assert ax_jwt.get_jwks() == first
    ax_jwt._get_signing_key.cache_clear()


def test_configured_public_issuer_is_used_by_signer_and_verifier(session, monkeypatch):
    from app.core.jwt_verify import _decode_backend_token
    client, _, user = session
    monkeypatch.setenv('AX_JWT_ISSUER', 'https://waystation.example')
    response = client.post('/auth/local/login', json={'username':'tester','password':'correct-test-password'})
    assert response.status_code == 200
    token = response.json()['access_token']
    claims = _decode_backend_token(token)
    assert claims['iss'] == 'https://waystation.example'
    monkeypatch.setenv('AX_JWT_ISSUER', 'https://another.example')
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _decode_backend_token(token)
    assert exc.value.status_code == 401
