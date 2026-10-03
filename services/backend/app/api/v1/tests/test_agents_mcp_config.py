import os
import json
import re
import uuid
import pytest
from httpx import AsyncClient

from api.main import app


def _get_server_key(mcp_config: dict) -> str:
    assert "mcpServers" in mcp_config and isinstance(mcp_config["mcpServers"], dict)
    keys = list(mcp_config["mcpServers"].keys())
    assert len(keys) == 1, "Expected a single server key"
    return keys[0]


def _args_index(args, flag):
    try:
        return args.index(flag)
    except ValueError:
        return -1


@pytest.mark.asyncio
async def test_register_agent_returns_oauth_mcp_config(monkeypatch):
    # Ensure environment reflects production for ax-platform label
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("API_URL", "https://paxai.app")

    # Mock auth dependency to simulate a logged-in user
    from app.core.jwt_verify import get_current_user_from_token

    class UserMock:
        id = "11111111-1111-1111-1111-111111111111"
        username = "tester"
        email = "t@example.com"
        current_space_id = "22222222-2222-2222-2222-222222222222"
        space_id = "22222222-2222-2222-2222-222222222222"
        role = "user"

    async def user_dep():
        return UserMock()

    app.dependency_overrides[get_current_user_from_token] = user_dep

    # Mock DB session provider to bypass real DB
    from app.core.database import get_db_session

    class DummyAgent:
        def __init__(self, name):
            self.id = "33333333-3333-3333-3333-333333333333"
            self.user_id = UserMock.id
            self.space_id = UserMock.space_id
            self.name = name
            self.description = None
            self.agent_type = "mcp"
            self.capabilities = {}
            self.status = "active"
            self.visibility_level = "org_visible"
            self.api_token_hash = "dummy"
            self.reputation_score = 0
            self.total_jobs_completed = 0
            self.created_at = None
            self.updated_at = None

    class DummySession:
        async def execute(self, *args, **kwargs):
            class R:
                def scalar_one_or_none(self_inner):
                    return None
                def scalar(self_inner):
                    return None
            return R()
        async def commit(self):
            return None
        async def rollback(self):
            return None
        async def refresh(self, obj):
            return None
        def add(self, obj):
            return None

    async def db_dep():
        yield DummySession()

    app.dependency_overrides[get_db_session] = db_dep

    from httpx import ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/auth/agents/register", json={"name": "agent_name"})
        if resp.status_code != 200:
            print(f"Response status: {resp.status_code}")
            print(f"Response body: {resp.text}")
        assert resp.status_code == 200
        body = resp.json()
        assert "mcp_config" in body
        cfg = body["mcp_config"]
        assert "mcpServers" in cfg
        server_key = _get_server_key(cfg)
        # New labels: ax-local for dev, ax-platform for production
        assert server_key in ("ax-local", "ax-platform")
        server = cfg["mcpServers"][server_key]
        assert server.get("command") == "npx"
        assert "env" not in server

        # Simplified format: just version and URL
        args = server.get("args", [])
        assert len(args) == 2
        assert args[0] == "mcp-remote@0.1.37"
        assert re.match(r"https?://.+/mcp/(agent|agents)/" + re.escape("agent_name") + r"$", args[1])

    # Cleanup overrides
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_agent_config_returns_oauth_config_dev(test_client: AsyncClient, auth_headers: dict):
    agent_name = f"CfgAgent_{uuid.uuid4().hex[:8]}"
    payload = {"name": agent_name, "description": "Config test", "agent_type": "general", "capabilities": {}}
    resp = await test_client.post("/auth/agents/register", json=payload, headers=auth_headers)
    assert resp.status_code == 200, resp.text

    lst = await test_client.get("/auth/agents", headers=auth_headers)
    assert lst.status_code == 200, lst.text
    agents = lst.json()["agents"]
    agent = next((a for a in agents if a.get("agent_name") == agent_name), None)
    assert agent is not None

    cfg_resp = await test_client.get(f"/auth/agents/{agent['id']}/config", headers=auth_headers)
    assert cfg_resp.status_code == 200, cfg_resp.text
    cfg_data = cfg_resp.json()

    cfg = cfg_data["mcp_config"]
    server_key = _get_server_key(cfg)
    assert server_key in ("ax-local", "ax-platform")
    server = cfg["mcpServers"][server_key]
    args = server.get("args", [])

    # Simplified format: just version and URL
    assert len(args) == 2
    assert args[0] == "mcp-remote@0.1.37"
    assert re.match(r"https?://.+/mcp/(agent|agents)/" + re.escape(agent_name) + r"$", args[1])


# Note: test_generate_oauth_mcp_config_script_env_variants was removed
# The generate_oauth_mcp_config module it referenced does not exist.
# Config generation is now tested via the _build_agent_mcp_config function
# in tests/test_mcp_config_version.py
