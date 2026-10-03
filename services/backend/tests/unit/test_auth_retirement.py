"""Only built-in human sessions and human-sponsored OAuth are runtime auth paths."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from api.main import app
from app.api.v1 import api_v1, oauth_as
from app.core import jwt_verify


def test_running_distribution_does_not_mount_pat_or_agent_key_routers():
    # OpenAPI represents the mounted HTTP surface across FastAPI router implementations.
    paths = set(app.openapi()["paths"])
    assert "/auth/local/login" in paths and "/oauth/token" in paths
    assert "/api/v1/credentials/violations" in paths  # audit data is preserved
    assert "/auth/exchange" not in paths
    assert "/api/v1/keys" not in paths
    assert "/credentials/agent-pat" not in paths
    assert not any(path.startswith("/api/v1/agents/") and "/keys" in path for path in paths)


@pytest.mark.asyncio
async def test_client_credentials_cannot_issue_even_with_a_valid_looking_secret():
    body = b"grant_type=client_credentials&client_id=old-agent&client_secret=old-secret"
    req = Request({"type": "http", "method": "POST", "path": "/oauth/token", "headers":
        [(b"content-type", b"application/x-www-form-urlencoded")]},
        receive=AsyncMock(return_value={"type": "http.request", "body": body, "more_body": False}))
    db = AsyncMock()
    response = await oauth_as.token_endpoint(req, db)
    assert response.status_code == 400
    assert json.loads(response.body)["error"] == "unsupported_grant_type"
    db.execute.assert_not_called()
    db.commit.assert_not_called()


def test_client_credentials_is_neither_advertised_nor_registerable():
    assert "client_credentials" not in oauth_as.GRANT_TYPES_SUPPORTED
    with pytest.raises(HTTPException) as exc:
        oauth_as._validate_client_metadata(oauth_as.ClientRegistrationRequest(
            client_name="Old flow", redirect_uris=[], grant_types=["client_credentials"], response_types=[]))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["axp_u_old.secret", "axat_old", "hmac-session", ""])
async def test_legacy_bearers_cannot_be_restored_by_old_flag(token, monkeypatch):
    monkeypatch.setenv("ENABLE_LEGACY_JWT", "true")
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await jwt_verify._resolve_user_from_bearer_token(token, db, allow_agent_tokens=True)
    assert exc.value.status_code == 401
    db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("claims", [
    {"token_class": "agent_access", "src_credential_id": "old-pat-uuid", "agent_id": "old-agent"},
    {"token_class": "user_admin", "src_credential_id": "old-pat-uuid"},
    {"agent_id": "old-m2m-agent"},
])
async def test_legacy_signed_token_classes_do_not_become_sponsored_oauth(claims, monkeypatch):
    monkeypatch.setattr(jwt_verify, "_decode_backend_token", lambda token: claims)
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await jwt_verify._resolve_user_from_bearer_token("signed.token.fixture", db, allow_agent_tokens=True)
    assert exc.value.status_code == 401
    db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["authorization_code:test", "device:test", "refresh:test"])
async def test_all_sponsored_oauth_flows_use_existing_authorization_checks(source, monkeypatch):
    claims = {"token_class": "agent_access", "src_credential_id": source}
    monkeypatch.setattr(jwt_verify, "_decode_backend_token", lambda token: claims)
    resolve = AsyncMock(return_value=SimpleNamespace(id="sponsor"))
    monkeypatch.setattr(jwt_verify, "_resolve_exchange_jwt_user", resolve)
    db = AsyncMock()
    await jwt_verify._resolve_user_from_bearer_token("signed.token.fixture", db, allow_agent_tokens=True)
    resolve.assert_awaited_once_with(claims, db)
    with pytest.raises(HTTPException):
        await jwt_verify._resolve_user_from_bearer_token("signed.token.fixture", db, allow_agent_tokens=False)


@pytest.mark.asyncio
async def test_human_sessions_keep_the_existing_account_session_checks(monkeypatch):
    claims = {"user_id": "human"}
    monkeypatch.setattr(jwt_verify, "_decode_backend_token", lambda token: claims)
    resolve = AsyncMock(return_value=SimpleNamespace(id="human", current_space_id="space", space_id="space"))
    monkeypatch.setattr(jwt_verify, "_resolve_builtin_user", resolve)
    db = AsyncMock()
    await jwt_verify._resolve_user_from_bearer_token("signed.token.fixture", db, allow_agent_tokens=False)
    resolve.assert_awaited_once_with(claims, db)


@pytest.mark.parametrize("mode", ["with_credentials", "sandbox"])
def test_new_drafts_cannot_mint_pat_credentials(mode):
    body = api_v1.AgentDraftCreateRequest(agent_mode=mode, agent={"name": "test-agent"},
        credential={"kind": "durable"})
    with pytest.raises(HTTPException) as exc:
        api_v1._normalize_agent_draft_payload(body, default_space_id="test", origin_space_id="test", origin_space_type="personal")
    assert exc.value.status_code == 410


@pytest.mark.asyncio
async def test_existing_credential_drafts_cannot_create_agents_or_mint_tokens():
    proposal = SimpleNamespace(proposed_payload={"kind": "agents.create.with_credentials", "credential": {"kind": "durable"}})
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api_v1._execute_agent_draft(db, proposal=proposal, approver_user_id="human")
    assert exc.value.status_code == 410
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_rls_dependency_cannot_restore_pat_through_old_exchange_flag(monkeypatch):
    from app.core.rls import get_secure_session
    monkeypatch.setenv("AX_ENFORCE_EXCHANGE", "false")
    req = Request({"type": "http", "method": "GET", "path": "/api/v1/tasks", "headers": []})
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await get_secure_session(req, token="axp_u_old.secret", db=db)
    assert exc.value.status_code == 401
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_developer_sessions_cannot_be_restored_through_old_testing_flag(monkeypatch):
    monkeypatch.setenv("ENABLE_LOCAL_TESTING", "true")
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await jwt_verify._resolve_builtin_user({"user_id": "old-test-user"}, db)
    assert exc.value.status_code == 401
    db.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("is_agent", [False, True])
async def test_rls_uses_canonical_verifier_and_preserves_actor_context(monkeypatch, is_agent):
    from app.core import rls
    user = SimpleNamespace(id="human", current_space_id="home", space_id="home", _effective_space_id="approved",
        _principal_type="agent" if is_agent else "user", _agent_id="agent", _agent_name="helper")
    resolve = AsyncMock(return_value=user)
    monkeypatch.setattr(jwt_verify, "_resolve_user_from_bearer_token", resolve)
    context = AsyncMock(); monkeypatch.setattr(rls, "set_rls_context", context)
    req = Request({"type": "http", "method": "GET", "path": "/api/v1/tasks", "headers": []})
    db = AsyncMock(); session = await rls.get_secure_session(req, token="session", db=db)
    resolve.assert_awaited_once_with("session", db, allow_agent_tokens=True, request=req)
    context.assert_awaited_once_with(db, user_id="human", space_id="approved", agent_id="agent" if is_agent else None)
    assert session.is_agent is is_agent
    assert session.principal_id == ("agent" if is_agent else "human")
