import base64
import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from itertools import count
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock

import jose.jwt as jose_jwt
import pytest

pytestmark = pytest.mark.integration
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.main import app
from app.api.v1 import oauth_as
from app.core.database import AsyncSessionLocal, engine
from app.core.security import get_password_hash, hash_token
from app.models.agent import Agent
from app.models.oauth_as import OAuthClient, OAuthDeviceCode, OAuthRefreshToken
from app.models.space import Space
from app.models.space_membership import SpaceMembership
from app.models.user import User


client = TestClient(app, base_url="http://localhost:8001")
_transport_counter = count(10)


@asynccontextmanager
async def async_oauth_client() -> AsyncIterator[AsyncClient]:
    await engine.dispose()
    async with AsyncClient(
        transport=ASGITransport(
            app=app,
            client=(f"203.0.113.{next(_transport_counter)}", 123),
        ),
        base_url="http://localhost:8001",
    ) as async_client:
        yield async_client
    await engine.dispose()


async def register_device_client(
    async_client: AsyncClient,
    scope: str = "openid messages.read spaces.read offline_access",
) -> str:
    response = await async_client.post(
        "/oauth/register",
        json={
            "client_name": "MCPJam device test client",
            "redirect_uris": [],
            "grant_types": [oauth_as.DEVICE_CODE_GRANT, "refresh_token"],
            "response_types": [],
            "token_endpoint_auth_method": "none",
            "scope": scope,
        },
    )
    assert response.status_code == 201
    return response.json()["client_id"]


async def create_oauth_sponsor_user(
    *,
    owner_user_id: uuid.UUID,
    space_id: uuid.UUID,
    username: str,
) -> None:
    async with AsyncSessionLocal() as db:
        space = Space(
            id=space_id,
            name=f"{username}'s Workspace",
            slug=f"{username}-workspace",
            visibility="private",
        )
        user = User(
            id=owner_user_id,
            space_id=space_id,
            current_space_id=space_id,
            email=f"{username}@example.test",
            password_hash="",
            username=username,
            full_name=username,
            active=True,
        )
        membership = SpaceMembership(
            user_id=owner_user_id,
            space_id=space_id,
            role="admin",
        )
        db.add(space)
        await db.flush()
        db.add(user)
        db.add(membership)
        await db.commit()


async def move_oauth_sponsor_current_space(
    *,
    owner_user_id: uuid.UUID,
    space_id: uuid.UUID,
    username: str,
) -> None:
    async with AsyncSessionLocal() as db:
        space = Space(
            id=space_id,
            name=f"{username}'s Second Workspace",
            slug=f"{username}-second-workspace",
            visibility="private",
        )
        membership = SpaceMembership(
            user_id=owner_user_id,
            space_id=space_id,
            role="admin",
        )
        user = await db.get(User, owner_user_id)
        db.add(space)
        await db.flush()
        user.current_space_id = space_id
        db.add(membership)
        await db.commit()


async def issue_pkce_refresh_token(
    async_client: AsyncClient,
    client_scope: str = "messages.read spaces.read",
    requested_scope: str = "messages.read spaces.read",
) -> tuple[str, str]:
    class FakeUser:
        id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    class FakeSecureSession:
        user = FakeUser()

    async def fake_secure_session():
        return FakeSecureSession()

    register_response = await async_client.post(
        "/oauth/register",
        json={
            "client_name": "MCPJam Inspector",
            "redirect_uris": ["http://127.0.0.1:6274/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": client_scope,
        },
    )
    assert register_response.status_code == 201
    client_id = register_response.json()["client_id"]
    code_verifier = "verifier-for-local-mcpjam-proof"
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")

    app.dependency_overrides[oauth_as.get_secure_session] = fake_secure_session
    try:
        authorize_response = await async_client.get(
            "/oauth/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": "http://127.0.0.1:6274/callback",
                "scope": requested_scope,
                "state": "state-123",
                "resource": "http://localhost:8002/mcp",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_secure_session, None)

    assert authorize_response.status_code == 302
    redirect = urlparse(authorize_response.headers["location"])
    query = parse_qs(redirect.query)

    token_response = await async_client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "client_id": client_id,
            "redirect_uri": "http://127.0.0.1:6274/callback",
            "code_verifier": code_verifier,
        },
    )
    assert token_response.status_code == 200
    return client_id, token_response.json()["refresh_token"]


def test_authorization_server_metadata_supports_optional_cognito_contract(monkeypatch):
    monkeypatch.setenv("AX_AUTH_SERVER_METADATA_MODE", "cognito")
    response = client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    data = response.json()

    assert data["issuer"] == "http://localhost:8001"
    assert data["authorization_endpoint"] == "http://localhost:8001/authorize"
    assert data["token_endpoint"] == "http://localhost:8001/token"
    assert data["registration_endpoint"] == "http://localhost:8001/register"
    assert data["revocation_endpoint"] == "http://localhost:8001/revoke"
    assert data["scopes_supported"] == ["openid"]
    assert data["grant_types_supported"] == ["authorization_code", "refresh_token"]
    assert data["code_challenge_methods_supported"] == ["S256"]


def test_authorization_server_metadata_advertises_headless_ax_as_contract(monkeypatch):
    monkeypatch.setenv("AX_AUTH_SERVER_METADATA_MODE", "ax")

    response = client.get("/.well-known/oauth-authorization-server")

    assert response.status_code == 200
    data = response.json()

    assert data["issuer"] == "http://localhost:8001"
    assert data["authorization_endpoint"] == "http://localhost:8001/oauth/authorize"
    assert data["token_endpoint"] == "http://localhost:8001/oauth/token"
    assert data["registration_endpoint"] == "http://localhost:8001/oauth/register"
    assert data["device_authorization_endpoint"] == "http://localhost:8001/oauth/device/code"
    assert data["jwks_uri"] == "http://localhost:8001/.well-known/jwks.json"
    assert data["service_documentation"] == "http://localhost:8001/auth.md"
    assert data["mcp"]["resource"] == "http://localhost:8002/mcp"
    assert data["mcp"]["agent_url_template"] == "http://localhost:8002/mcp/agents/{agent_name}"
    assert any(
        method.get("type") == "header" and method.get("header") == "X-Agent-Name"
        for method in data["mcp"]["agent_binding_methods"]
    )
    assert "client_credentials" in data["grant_types_supported"]
    assert oauth_as.DEVICE_CODE_GRANT in data["grant_types_supported"]
    assert data["code_challenge_methods_supported"] == ["S256"]


def test_protected_resource_metadata_points_mcp_resource_to_ax_as():
    response = client.get(
        "/.well-known/oauth-protected-resource",
        params={"resource": "http://localhost:8002/mcp"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["resource"] == "http://localhost:8002/mcp"
    assert data["resource_name"] == "aX MCP"
    assert data["authorization_servers"] == ["http://localhost:8001"]
    assert data["bearer_methods_supported"] == ["header"]
    assert data["resource_documentation"] == "http://localhost:8001/auth.md"
    assert data["mcp"]["agent_url_template"] == "http://localhost:8002/mcp/agents/{agent_name}"
    assert any(
        method.get("type") == "url_path"
        and method.get("template") == "/mcp/agents/{agent_name}"
        for method in data["mcp"]["agent_binding_methods"]
    )
    assert any(
        method.get("type") == "header" and method.get("header") == "X-Agent-Name"
        for method in data["mcp"]["agent_binding_methods"]
    )
    assert any(
        method.get("type") == "header" and method.get("header") == "X-Agent-Id"
        for method in data["mcp"]["agent_binding_methods"]
    )
    assert "messages.read" in data["scopes_supported"]


def test_auth_md_documents_headless_device_flow_and_agent_binding():
    response = client.get("/auth.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    body = response.text

    assert "device_authorization_endpoint" in body
    assert "verification_uri_complete" in body
    assert "/mcp/agents/{agent_name}" in body
    assert "X-Agent-Name" in body
    assert "X-Agent-Id" in body
    assert "Do not use the base" in body
    assert "refresh_token" in body


def test_device_approval_page_forwards_browser_jwt_to_approval_endpoint():
    response = client.get("/device", params={"user_code": "ABCD1234"})

    assert response.status_code == 200
    body = response.text

    assert "aX MCP Device Approval" in body
    assert 'data-device-agent-name' in body
    assert 'data-device-client-name' in body
    assert 'data-device-scopes' in body
    assert 'fetch("/oauth/device/verify' in body
    assert 'name="jwt_token"' in body
    assert "localStorage" in body
    assert "sessionStorage" in body
    assert "accessToken" in body
    assert "idToken" in body
    assert 'fetch("/oauth/device/approve"' in body
    assert "approvalBlocked" in body
    assert "This device code is not agent-scoped" in body
    assert "/mcp/agents/{agent_name}" in body
    assert "button.disabled" not in body
    assert 'value="ABCD1234"' in body


def test_device_approve_get_redirects_to_human_approval_page():
    response = client.get(
        "/oauth/device/approve",
        params={"user_code": "ABCD1234"},
        follow_redirects=False,
    )

    assert response.status_code == 307
    assert response.headers["location"] == "/device?user_code=ABCD1234"


@pytest.mark.asyncio
async def test_dynamic_client_registration_accepts_loopback_public_client():
    async with async_oauth_client() as async_client:
        response = await async_client.post(
            "/oauth/register",
            json={
                "client_name": "MCPJam Inspector",
                "redirect_uris": ["http://127.0.0.1:6274/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "openid messages.read",
            },
        )

    assert response.status_code == 201
    data = response.json()

    assert data["client_id"].startswith("ax_dcr_")
    assert data["redirect_uris"] == ["http://127.0.0.1:6274/callback"]
    assert data["token_endpoint_auth_method"] == "none"
    assert data["grant_types"] == ["authorization_code", "refresh_token"]
    assert "client_secret" not in data


@pytest.mark.asyncio
async def test_public_dcr_client_granted_offline_access_and_mcp_floor():
    """Regression: Claude Code-style public DCR registers narrowly (legacy
    discovery advertises only "openid"), then requests offline_access at
    /authorize and hit 400 invalid_scope. Public clients must be granted the MCP
    floor (offline_access + ax-api/mcp:read/write) so the authorize subset check
    passes."""
    async with async_oauth_client() as async_client:
        response = await async_client.post(
            "/oauth/register",
            json={
                "client_name": "Claude Code",
                "redirect_uris": ["http://127.0.0.1:7777/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "openid",
            },
        )

    assert response.status_code == 201
    granted = set(response.json()["scope"].split())
    assert {"openid", "offline_access", "ax-api/mcp:read", "ax-api/mcp:write"} <= granted


def test_client_registration_response_serializes_unset_scope_as_string():
    client = OAuthClient(
        client_id="ax_dcr_scope_less",
        client_name="Scope-less public client",
        redirect_uris=["http://127.0.0.1:7788/callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=None,
    )

    data = oauth_as._client_registration_response(client, issued_at=123)

    assert data["scope"] == ""
    assert client.scope is None


@pytest.mark.asyncio
async def test_scope_less_public_dcr_not_narrowed_to_floor():
    """A public DCR registration that omits `scope` must keep client.scope unset
    so it preserves authorization-time scope selection. Forcing the MCP floor
    here would regress clients that legitimately request other supported scopes
    at /authorize (P2)."""
    async with async_oauth_client() as async_client:
        response = await async_client.post(
            "/oauth/register",
            json={
                "client_name": "Scope-less public client",
                "redirect_uris": ["http://127.0.0.1:7788/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["scope"] == ""  # wire response is string-valued, not JSON null

    async with AsyncSessionLocal() as db:
        client = await db.get(OAuthClient, data["client_id"])
        assert client is not None
        assert client.scope is None  # stored registration stays unrestricted


@pytest.mark.asyncio
async def test_dynamic_client_registration_rejects_non_loopback_http_redirect():
    async with async_oauth_client() as async_client:
        response = await async_client.post(
            "/oauth/register",
            json={
                "client_name": "Bad Client",
                "redirect_uris": ["http://evil.example/callback"],
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_redirect_uri"


@pytest.mark.asyncio
async def test_device_authorization_issues_user_code_and_verification_link():
    async with async_oauth_client() as async_client:
        client_id = await register_device_client(async_client)
        response = await async_client.post(
            "/oauth/device/code",
            data={
                "client_id": client_id,
                "scope": "openid messages.read",
                "resource": "http://localhost:8002/mcp/agents/Lantern",
            },
        )

    assert response.status_code == 200
    data = response.json()

    assert data["device_code"].startswith("dev_")
    assert len(data["user_code"]) == 8
    assert data["verification_uri"] == "http://localhost:8001/device"
    assert data["verification_uri_complete"].startswith("http://localhost:8001/device?user_code=")
    assert data["expires_in"] > 0
    assert data["interval"] >= 5


@pytest.mark.asyncio
async def test_device_authorization_rejects_empty_named_agent_resource():
    async with async_oauth_client() as async_client:
        client_id = await register_device_client(async_client)
        response = await async_client.post(
            "/oauth/device/code",
            data={
                "client_id": client_id,
                "scope": "openid messages.read",
                "resource": "http://localhost:8002/mcp/agents/",
            },
        )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_target"


@pytest.mark.asyncio
async def test_device_authorization_rejects_bare_mcp_resource_for_agent_onboarding():
    async with async_oauth_client() as async_client:
        client_id = await register_device_client(async_client)
        response = await async_client.post(
            "/oauth/device/code",
            data={
                "client_id": client_id,
                "scope": "openid messages.read",
                "resource": "http://localhost:8002/mcp",
            },
        )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "invalid_target"
    assert "/mcp/agents/{agent_name}" in detail["error_description"]


@pytest.mark.asyncio
async def test_device_authorization_can_advertise_frontend_verification_url(monkeypatch):
    monkeypatch.setenv(
        "AX_DEVICE_VERIFICATION_URL",
        "https://paxai.app/auth/device/verify",
    )

    async with async_oauth_client() as async_client:
        client_id = await register_device_client(async_client)
        response = await async_client.post(
            "/oauth/device/code",
            data={
                "client_id": client_id,
                "scope": "openid messages.read",
                "resource": "http://localhost:8002/mcp/agents/Lantern",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["verification_uri"] == "https://paxai.app/auth/device/verify"
    assert data["verification_uri_complete"].startswith(
        "https://paxai.app/auth/device/verify?user_code="
    )


@pytest.mark.asyncio
async def test_device_verification_endpoint_returns_pending_request_details():
    async with async_oauth_client() as async_client:
        client_id = await register_device_client(async_client)
        device_response = await async_client.post(
            "/oauth/device/code",
            data={
                "client_id": client_id,
                "scope": "messages.read spaces.read",
                "resource": "http://localhost:8002/mcp/agents/Lantern",
            },
        )
        device_data = device_response.json()

        response = await async_client.get(
            "/oauth/device/verify",
            params={"user_code": device_data["user_code"]},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    assert data["user_code"] == device_data["user_code"]
    assert data["client_id"] == client_id
    assert data["client_name"] == "MCPJam device test client"
    assert data["scopes"] == ["messages.read", "spaces.read"]
    assert data["expires_at"]
    assert data["resource"] == "http://localhost:8002/mcp/agents/Lantern"
    assert data["agent_name"] == "Lantern"
    assert data["resource_type"] == "mcp_agent"
    assert data["approval_blocked"] is False


@pytest.mark.asyncio
async def test_device_verification_blocks_existing_bare_mcp_resource_and_approval():
    async def fake_device_approval_principal():
        return oauth_as.DeviceApprovalPrincipal(
            user_id="cccccccc-cccc-cccc-cccc-cccccccccccc"
        )

    user_code = uuid.uuid4().hex[:8].upper()
    async with async_oauth_client() as async_client:
        client_id = await register_device_client(async_client)
        async with AsyncSessionLocal() as db:
            db.add(
                OAuthDeviceCode(
                    device_code_hash=hash_token(f"dev_existing_bare_mcp_{user_code}"),
                    user_code_hash=hash_token(user_code),
                    client_id=client_id,
                    scope="messages.read spaces.read",
                    resource="http://localhost:8002/mcp",
                    status="pending",
                    expires_at=oauth_as._expires_in(600),
                )
            )
            await db.commit()

        verify_response = await async_client.get(
            "/oauth/device/verify",
            params={"user_code": user_code},
        )

        app.dependency_overrides[oauth_as.get_device_approval_principal] = (
            fake_device_approval_principal
        )
        try:
            approval_response = await async_client.post(
                "/oauth/device/approve",
                json={"user_code": user_code},
            )
        finally:
            app.dependency_overrides.pop(oauth_as.get_device_approval_principal, None)

    assert verify_response.status_code == 200
    verify_data = verify_response.json()
    assert verify_data["resource_type"] == "mcp"
    assert verify_data["approval_blocked"] is True
    assert verify_data["error"] == "invalid_target"
    assert "/mcp/agents/{agent_name}" in verify_data["error_description"]

    assert approval_response.status_code == 400
    detail = approval_response.json()["detail"]
    assert detail["error"] == "invalid_target"
    assert "/mcp/agents/{agent_name}" in detail["error_description"]


@pytest.mark.asyncio
async def test_device_verification_endpoint_returns_named_agent_details():
    async with async_oauth_client() as async_client:
        client_id = await register_device_client(async_client)
        device_response = await async_client.post(
            "/oauth/device/code",
            data={
                "client_id": client_id,
                "scope": "messages.read spaces.read",
                "resource": "http://localhost:8002/mcp/agents/Lantern",
            },
        )
        device_data = device_response.json()

        response = await async_client.get(
            "/oauth/device/verify",
            params={"user_code": device_data["user_code"]},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["resource"] == "http://localhost:8002/mcp/agents/Lantern"
    assert data["agent_name"] == "Lantern"
    assert data["resource_type"] == "mcp_agent"


@pytest.mark.asyncio
async def test_device_token_poll_returns_authorization_pending_before_approval():
    async with async_oauth_client() as async_client:
        client_id = await register_device_client(async_client)
        device_response = await async_client.post(
            "/oauth/device/code",
            data={
                "client_id": client_id,
                "resource": "http://localhost:8002/mcp/agents/Lantern",
            },
        )
        device_code = device_response.json()["device_code"]

        response = await async_client.post(
            "/oauth/token",
            data={
                "grant_type": oauth_as.DEVICE_CODE_GRANT,
                "device_code": device_code,
                "client_id": client_id,
            },
        )

    assert response.status_code == 400
    assert response.json()["error"] == "authorization_pending"


@pytest.mark.asyncio
async def test_device_denial_returns_access_denied_to_polling_client():
    async def fake_device_approval_principal():
        return oauth_as.DeviceApprovalPrincipal(
            user_id="cccccccc-cccc-cccc-cccc-cccccccccccc"
        )

    async with async_oauth_client() as async_client:
        client_id = await register_device_client(async_client)
        device_response = await async_client.post(
            "/oauth/device/code",
            data={
                "client_id": client_id,
                "scope": "messages.read spaces.read",
                "resource": "http://localhost:8002/mcp/agents/Lantern",
            },
        )
        device_data = device_response.json()

        app.dependency_overrides[oauth_as.get_device_approval_principal] = (
            fake_device_approval_principal
        )
        try:
            approval_response = await async_client.post(
                "/oauth/device/approve",
                json={"user_code": device_data["user_code"], "approved": False},
            )
        finally:
            app.dependency_overrides.pop(oauth_as.get_device_approval_principal, None)

        token_response = await async_client.post(
            "/oauth/token",
            data={
                "grant_type": oauth_as.DEVICE_CODE_GRANT,
                "device_code": device_data["device_code"],
                "client_id": client_id,
            },
        )

    assert approval_response.status_code == 200
    assert approval_response.json()["status"] == "denied"
    assert token_response.status_code == 400
    assert token_response.json()["error"] == "access_denied"


@pytest.mark.asyncio
async def test_device_approval_then_token_poll_mints_user_access_jwt():
    async def fake_device_approval_principal():
        return oauth_as.DeviceApprovalPrincipal(
            user_id="cccccccc-cccc-cccc-cccc-cccccccccccc"
        )

    async with async_oauth_client() as async_client:
        client_id = await register_device_client(async_client)
        device_response = await async_client.post(
            "/oauth/device/code",
            data={
                "client_id": client_id,
                "scope": "messages.read spaces.read",
                "resource": "http://localhost:8002/api",
            },
        )
        device_data = device_response.json()

        app.dependency_overrides[oauth_as.get_device_approval_principal] = (
            fake_device_approval_principal
        )
        try:
            approval_response = await async_client.post(
                "/oauth/device/approve",
                json={"user_code": device_data["user_code"]},
            )
        finally:
            app.dependency_overrides.pop(oauth_as.get_device_approval_principal, None)

        assert approval_response.status_code == 200
        assert approval_response.json()["status"] == "approved"

        token_response = await async_client.post(
            "/oauth/token",
            data={
                "grant_type": oauth_as.DEVICE_CODE_GRANT,
                "device_code": device_data["device_code"],
                "client_id": client_id,
            },
        )

    assert token_response.status_code == 200
    data = token_response.json()
    claims = jose_jwt.get_unverified_claims(data["access_token"])

    assert data["token_type"] == "Bearer"
    assert data["scope"] == "messages.read spaces.read"
    assert claims["iss"] == "ax-backend"
    assert claims["sub"] == "user:cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert claims["token_class"] == "user_access"
    assert claims["owner_user_id"] == "cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert claims["aud"] == "http://localhost:8002/api"
    assert claims["scope"] == "messages.read spaces.read"


@pytest.mark.asyncio
async def test_device_agent_resource_mints_agent_access_jwt_and_refresh_preserves_agent():
    owner_user_id = uuid.uuid4()
    space_id = uuid.uuid4()
    username = f"oauth-sponsor-{uuid.uuid4().hex[:8]}"
    agent_name = f"lantern-{uuid.uuid4().hex[:8]}"
    resource = f"http://localhost:8002/mcp/agents/{agent_name}"
    await create_oauth_sponsor_user(
        owner_user_id=owner_user_id,
        space_id=space_id,
        username=username,
    )

    async def fake_device_approval_principal():
        return oauth_as.DeviceApprovalPrincipal(user_id=str(owner_user_id))

    async with async_oauth_client() as async_client:
        client_id = await register_device_client(
            async_client,
            scope="openid offline_access messages.read messages.write spaces.read",
        )
        device_response = await async_client.post(
            "/oauth/device/code",
            data={
                "client_id": client_id,
                "scope": "openid offline_access messages.read messages.write spaces.read",
                "resource": resource,
            },
        )
        device_data = device_response.json()

        app.dependency_overrides[oauth_as.get_device_approval_principal] = (
            fake_device_approval_principal
        )
        try:
            approval_response = await async_client.post(
                "/oauth/device/approve",
                json={"user_code": device_data["user_code"]},
            )
        finally:
            app.dependency_overrides.pop(oauth_as.get_device_approval_principal, None)

        token_response = await async_client.post(
            "/oauth/token",
            data={
                "grant_type": oauth_as.DEVICE_CODE_GRANT,
                "device_code": device_data["device_code"],
                "client_id": client_id,
            },
        )

        assert approval_response.status_code == 200
        assert token_response.status_code == 200
        token_data = token_response.json()
        claims = jose_jwt.get_unverified_claims(token_data["access_token"])

        async with AsyncSessionLocal() as db:
            agent_result = await db.execute(
                select(Agent).where(
                    Agent.user_id == owner_user_id,
                    Agent.space_id == space_id,
                    Agent.name == agent_name,
                )
            )
            agent = agent_result.scalar_one()
            refresh_record_result = await db.execute(
                select(OAuthRefreshToken).where(
                    OAuthRefreshToken.refresh_token_hash == hash_token(
                        token_data["refresh_token"]
                    )
                )
            )
            refresh_record = refresh_record_result.scalar_one()
            assert refresh_record.agent_id == agent.id

        await move_oauth_sponsor_current_space(
            owner_user_id=owner_user_id,
            space_id=uuid.uuid4(),
            username=username,
        )

        refresh_response = await async_client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": token_response.json()["refresh_token"],
                "client_id": client_id,
            },
        )

    assert token_data["token_type"] == "Bearer"
    assert claims["iss"] == "ax-backend"
    assert claims["sub"] == f"agent:{agent.id}"
    assert claims["token_class"] == "agent_access"
    assert claims["agent_id"] == str(agent.id)
    assert claims["owner_user_id"] == str(owner_user_id)
    assert claims["aud"] == oauth_as._default_resource_url()
    assert claims["scope"] == "openid offline_access messages.read messages.write spaces.read"

    assert refresh_response.status_code == 200
    refresh_claims = jose_jwt.get_unverified_claims(refresh_response.json()["access_token"])
    assert refresh_claims["sub"] == f"agent:{agent.id}"
    assert refresh_claims["token_class"] == "agent_access"
    assert refresh_claims["agent_id"] == str(agent.id)


@pytest.mark.asyncio
async def test_oauth_agent_creation_race_requeries_existing_agent(monkeypatch):
    owner_user_id = uuid.uuid4()
    space_id = uuid.uuid4()
    agent_name = f"lantern-{uuid.uuid4().hex[:8]}"
    existing_agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=owner_user_id,
        space_id=space_id,
        name=agent_name,
        status="active",
    )
    user = SimpleNamespace(
        id=owner_user_id,
        active=True,
        current_space_id=space_id,
        space_id=space_id,
    )

    class FakeResult:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class FakeSavepoint:
        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    class FakeDb:
        def __init__(self):
            self.execute_calls = 0
            self.flush_calls = 0

        async def get(self, model, _key):
            if model is User:
                return user
            return None

        async def execute(self, _stmt):
            self.execute_calls += 1
            return FakeResult(None if self.execute_calls == 1 else existing_agent)

        def add(self, _obj):
            return None

        async def flush(self):
            self.flush_calls += 1
            if self.flush_calls == 1:
                raise IntegrityError("insert agent", {}, Exception("duplicate"))

        def begin_nested(self):
            return FakeSavepoint()

    grant_space_access = AsyncMock()
    monkeypatch.setattr(oauth_as, "grant_space_access", grant_space_access)
    db = FakeDb()

    agent = await oauth_as._resolve_or_create_oauth_agent(
        db,
        owner_user_id=str(owner_user_id),
        resource=f"http://localhost:8002/mcp/agents/{agent_name}",
    )

    assert agent is existing_agent
    grant_space_access.assert_awaited_once_with(
        db,
        existing_agent.id,
        space_id,
        is_default=True,
    )


@pytest.mark.asyncio
async def test_authorization_code_pkce_and_refresh_tokens_are_hashed_and_rotated():
    class FakeUser:
        id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    class FakeSecureSession:
        user = FakeUser()

    async def fake_secure_session():
        return FakeSecureSession()

    async with async_oauth_client() as async_client:
        register_response = await async_client.post(
            "/oauth/register",
            json={
                "client_name": "MCPJam Inspector",
                "redirect_uris": ["http://127.0.0.1:6274/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        client_id = register_response.json()["client_id"]
        code_verifier = "verifier-for-local-mcpjam-proof"
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")

        app.dependency_overrides[oauth_as.get_secure_session] = fake_secure_session
        try:
            authorize_response = await async_client.get(
                "/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": "http://127.0.0.1:6274/callback",
                    "scope": "messages.read spaces.read",
                    "state": "state-123",
                    "resource": "http://localhost:8002/mcp",
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                },
                follow_redirects=False,
            )
        finally:
            app.dependency_overrides.pop(oauth_as.get_secure_session, None)

        assert authorize_response.status_code == 302
        redirect = urlparse(authorize_response.headers["location"])
        query = parse_qs(redirect.query)
        assert query["state"] == ["state-123"]

        token_response = await async_client.post(
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "client_id": client_id,
                "redirect_uri": "http://127.0.0.1:6274/callback",
                "code_verifier": code_verifier,
            },
        )

        assert token_response.status_code == 200
        data = token_response.json()
        claims = jose_jwt.get_unverified_claims(data["access_token"])
        assert claims["sub"] == "user:dddddddd-dddd-dddd-dddd-dddddddddddd"
        assert claims["aud"] == "http://localhost:8002/mcp"

        async with AsyncSessionLocal() as db:
            stored = await db.execute(
                select(OAuthRefreshToken).where(
                    OAuthRefreshToken.refresh_token_hash == hash_token(data["refresh_token"])
                )
            )
            plaintext = await db.execute(
                select(OAuthRefreshToken).where(
                    OAuthRefreshToken.refresh_token_hash == data["refresh_token"]
                )
            )
            assert stored.scalar_one_or_none() is not None
            assert plaintext.scalar_one_or_none() is None

        refresh_response = await async_client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": data["refresh_token"],
                "client_id": client_id,
            },
        )

    assert refresh_response.status_code == 200
    refresh_data = refresh_response.json()
    refresh_claims = jose_jwt.get_unverified_claims(refresh_data["access_token"])

    assert refresh_data["refresh_token"] != data["refresh_token"]
    assert refresh_claims["sub"] == "user:dddddddd-dddd-dddd-dddd-dddddddddddd"
    assert refresh_claims["scope"] == "messages.read spaces.read"


@pytest.mark.asyncio
async def test_authorization_code_rejects_scope_outside_registered_client_scope():
    class FakeUser:
        id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    class FakeSecureSession:
        user = FakeUser()

    async def fake_secure_session():
        return FakeSecureSession()

    async with async_oauth_client() as async_client:
        register_response = await async_client.post(
            "/oauth/register",
            json={
                "client_name": "Narrow MCP Client",
                "redirect_uris": ["http://127.0.0.1:6274/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": "messages.read",
            },
        )
        client_id = register_response.json()["client_id"]
        code_verifier = "verifier-for-local-mcpjam-proof"
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        ).decode("ascii").rstrip("=")

        app.dependency_overrides[oauth_as.get_secure_session] = fake_secure_session
        try:
            authorize_response = await async_client.get(
                "/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": "http://127.0.0.1:6274/callback",
                    "scope": "messages.read messages.write",
                    "resource": "http://localhost:8002/mcp",
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                },
                follow_redirects=False,
            )
        finally:
            app.dependency_overrides.pop(oauth_as.get_secure_session, None)

    assert authorize_response.status_code == 400
    assert authorize_response.json()["detail"]["error"] == "invalid_scope"


@pytest.mark.asyncio
async def test_refresh_token_rejects_inactive_client():
    async with async_oauth_client() as async_client:
        client_id, refresh_token = await issue_pkce_refresh_token(async_client)

        async with AsyncSessionLocal() as db:
            client = await db.get(OAuthClient, client_id)
            assert client is not None
            client.is_active = False
            await db.commit()

        refresh_response = await async_client.post(
            "/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
        )

    assert refresh_response.status_code == 401
    assert refresh_response.json()["error"] == "invalid_client"


def test_client_credentials_token_mints_backend_agent_jwt():
    class FakeAgent:
        id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        name = "lantern"
        space_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    class FakeAgentKey:
        agent_id = FakeAgent.id
        client_secret_hash = get_password_hash("secret")
        scopes = "messages.read messages.write"
        is_active = True
        agent = FakeAgent()
        last_used_at = None

    class FakeResult:
        def scalar_one_or_none(self):
            return FakeAgentKey()

    class FakeSession:
        async def execute(self, _stmt):
            return FakeResult()

        async def commit(self):
            return None

    async def fake_db_session():
        return FakeSession()

    app.dependency_overrides[oauth_as.get_db_session] = fake_db_session
    try:
        response = client.post(
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "ax_test",
                "client_secret": "secret",
                "scope": "messages.read messages.write",
            },
        )
    finally:
        app.dependency_overrides.pop(oauth_as.get_db_session, None)

    assert response.status_code == 200
    data = response.json()
    claims = jose_jwt.get_unverified_claims(data["access_token"])

    assert data["token_type"] == "Bearer"
    assert data["scope"] == "messages.read messages.write"
    assert claims["iss"] == "ax-backend"
    assert claims["sub"] == "agent:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert claims["agent_name"] == "lantern"
    assert claims["space_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert claims["scope"] == "messages.read messages.write"
