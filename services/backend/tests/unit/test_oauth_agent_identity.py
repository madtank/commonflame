from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import jwt
import pytest

from app.api.v1 import oauth_as
from app.core import ax_jwt


@pytest.mark.asyncio
async def test_oauth_issuance_carries_the_database_agent_name_and_approved_workspace(monkeypatch):
    agent = SimpleNamespace(id=uuid.uuid4(), name="approved-sdk-agent", space_id=uuid.uuid4())
    monkeypatch.setattr(oauth_as, "_resolve_or_create_oauth_agent", AsyncMock(return_value=agent))
    monkeypatch.setattr(oauth_as, "_add_refresh_token", lambda *args, **kwargs: None)
    response = await oauth_as._issue_oauth_token_response(
        SimpleNamespace(), client_id="client-test", owner_user_id=str(uuid.uuid4()),
        scope="messages.write tasks.write", audience="http://localhost:3000/mcp",
        src_credential_id="oauth:client-test", authorized_space_id=str(agent.space_id),
    )
    _, public_key = ax_jwt._get_signing_key()
    claims = jwt.decode(response["access_token"], public_key, algorithms=["RS256"],
                        audience="http://localhost:3000/mcp", issuer=ax_jwt.get_issuer())
    assert claims["agent_id"] == str(agent.id)
    assert claims["agent_name"] == agent.name
    assert claims["space_id"] == claims["authorized_space_id"] == str(agent.space_id)
    assert claims["sub"] == f"agent:{agent.id}"


def test_exchange_without_workspace_does_not_invent_an_mcp_space_binding():
    token = ax_jwt.mint_exchange_jwt(
        sub="agent:test", token_class="agent_access", audience="ax-api", scope="",
        ttl_seconds=60, src_credential_id="test", owner_user_id="test", agent_id="test",
    )
    _, public_key = ax_jwt._get_signing_key()
    claims = jwt.decode(token, public_key, algorithms=["RS256"], audience="ax-api",
                        issuer=ax_jwt.get_issuer())
    assert "space_id" not in claims and "agent_name" not in claims
