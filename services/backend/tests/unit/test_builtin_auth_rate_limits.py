"""Builtin browser auth keeps IP limits independent for each operation."""

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import jwt
import pytest

from app.core import redis_rate_limiter
from app.middleware import rate_limiting_middleware as rate_limits


@pytest.fixture
def browser_auth_client(monkeypatch):
    # Exercise the actual fallback limiter without external Redis or shared
    # process state. Redis receives the same namespace before this fallback.
    monkeypatch.setattr(rate_limits, "rate_limit_store", rate_limits.RateLimitStore())
    monkeypatch.setattr(redis_rate_limiter, "check_allow", AsyncMock(side_effect=ConnectionError))
    monkeypatch.setenv("DEV_IP_BLOCK_ENABLED", "false")
    app = FastAPI()
    app.add_middleware(rate_limits.RateLimitingMiddleware)

    @app.get("/auth/local/status")
    async def status():
        return {"setup_required": False}

    @app.post("/auth/local/refresh")
    async def refresh():
        return JSONResponse({"detail": "Signed out"}, status_code=401)

    @app.post("/auth/local/login")
    async def login():
        return JSONResponse({"detail": "Invalid username or password"}, status_code=401)

    with TestClient(app, client=("127.0.0.1", 1234)) as client:
        yield client


@pytest.mark.parametrize("environment", ["local", "production"])
def test_status_and_refresh_cannot_exhaust_login_or_spoof_new_buckets(
    browser_auth_client, monkeypatch, environment
):
    monkeypatch.setenv("ENVIRONMENT", environment)
    client = browser_auth_client
    # A browser checks setup and restores its cookie before submitting login.
    # Repeated checks must not consume the restrictive login burst of two.
    for _ in range(3):
        assert client.get("/auth/local/status").status_code == 200
        assert client.post("/auth/local/refresh").status_code == 401

    login_keys = set()
    for number in range(2):
        forged_token = jwt.encode({"sub": f"forged-user-{number}", "role": "admin"}, "test-only-key", algorithm="HS256")
        response = client.post("/auth/local/login", headers={
            "X-Agent-Name": f"forged-agent-{number}",
            "X-Client-Instance": f"forged-instance-{number}",
            "X-Forwarded-For": f"198.51.100.{number + 1}",
            "Authorization": f"Bearer {forged_token}",
        })
        assert response.status_code == 401
        login_keys.add(response.headers["x-ratelimit-key"])

    blocked = client.post("/auth/local/login?client_id=another-forged-client", headers={
        "X-Agent-Name": "yet-another-agent",
        "X-Client-Instance": "yet-another-instance",
        "X-Forwarded-For": "203.0.113.99",
    })
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"] == "5"
    assert blocked.headers["x-ratelimit-key"] in login_keys
    assert len(login_keys) == 1
    # An exhausted login bucket also cannot block status or cookie restoration.
    assert client.get("/auth/local/status").status_code == 200
    assert client.post("/auth/local/refresh").status_code == 401


def test_builtin_auth_namespaces_use_only_the_fixed_operation_and_trusted_ip():
    middleware = rate_limits.RateLimitingMiddleware(lambda: None)
    operations = {"status", "login", "setup", "signup", "refresh", "invites", "logout"}
    keys = {middleware._apply_scope_namespace("ip:127.0.0.1", f"/auth/local/{operation}")
            for operation in operations}
    assert len(keys) == len(operations)
    assert middleware._apply_scope_namespace("ip:127.0.0.1", "/auth/local/login/") in keys
    assert middleware._apply_scope_namespace("ip:127.0.0.1", "/auth/local/unrecognized") == "ip:127.0.0.1:builtin-auth:other"
