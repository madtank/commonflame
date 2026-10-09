"""OAuth identities must not inherit a cloud model label in public responses."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import uuid

import pytest

from app.api.v1 import agents
from app.models.agent import Agent
from app.services.agent_control_service import AgentControlState
from app.schemas.agent import serialize_agent, DetailLevel


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["mcp", "external_gateway", "cloud"])
async def test_response_distinguishes_external_identity_from_cloud_runtime(monkeypatch, origin):
    now = datetime.now(timezone.utc)
    agent = Agent(
        id=uuid.uuid4(), user_id=uuid.uuid4(), space_id=uuid.uuid4(),
        name="sponsored-client", agent_type="mcp" if origin == "mcp" else "general",
        origin=origin, model=agents.DEFAULT_MODEL, status="active",
        capabilities={}, created_at=now, updated_at=now, lifecycle_state="active",
        last_active_at=now, web_browsing_enabled=False,
        ax_mcp_enabled=True, web_fetch_enabled=False, brave_search_enabled=False,
        image_gen_enabled=False,
    )
    monkeypatch.setattr(agents.agent_control_service, "get_control_state",
                        AsyncMock(return_value=AgentControlState()))
    response = (await agents._build_agent_response(agent)).model_dump()
    roster_response = serialize_agent(agent, detail=DetailLevel.summary)
    assert roster_response["model"] == response["model"]
    assert response["id"] == str(agent.id)
    assert response["origin"] == origin
    if origin == "cloud":
        assert response["model"] == agents.DEFAULT_MODEL
        assert response["model_tier"] is not None
    else:
        assert response["model"] is None
        assert response["model_tier"] is None
