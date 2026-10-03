"""
AX-AGENT-MGMT-001: Agent management auth extraction tests.

Tests:
- Actor extraction from SecureSession + request headers
- X-On-Behalf-Of delegation validation
- Concierge identity verification
- Mode determination logic

All mocks use call-counter ordering (not str(stmt) introspection)
to avoid SQLAlchemy mapper initialization issues.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.agent_management_auth import (
    AgentManagementActor,
    extract_management_actor,
)


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def make_session(
    *,
    user_id=None,
    space_id=None,
    agent_id=None,
    agent_name=None,
    is_agent=False,
):
    """Build a mock SecureSession."""
    session = MagicMock()
    session.is_agent = is_agent
    session.agent_id = str(agent_id) if agent_id else None
    session.agent_name = agent_name
    session.space_id = str(space_id) if space_id else str(uuid.uuid4())
    session.db = AsyncMock()

    if user_id:
        user = MagicMock()
        user.id = user_id
        session.user = user
    else:
        session.user = None if is_agent else MagicMock()
        if session.user and not user_id:
            session.user.id = uuid.uuid4()
    return session


def make_request(headers=None):
    """Build a mock Request."""
    req = MagicMock()
    req.headers = headers or {}
    req.client = MagicMock()
    req.client.host = "127.0.0.1"
    return req


def make_agent_model(
    *,
    agent_id=None,
    management_class="concierge",
    home_space_id=None,
):
    agent = MagicMock()
    agent.id = agent_id or uuid.uuid4()
    agent.management_class = management_class
    agent.home_space_id = home_space_id or uuid.uuid4()
    return agent


def make_membership(*, user_id, space_id, role="member"):
    m = MagicMock()
    m.user_id = user_id
    m.space_id = space_id
    m.role = role
    return m


def mock_db_returns(*returns):
    """Create mock db that returns values sequentially."""
    db = AsyncMock()
    idx = {"n": 0}

    async def execute_side_effect(stmt):
        result = MagicMock()
        i = idx["n"]
        idx["n"] += 1
        if i < len(returns):
            result.scalar_one_or_none.return_value = returns[i]
        else:
            result.scalar_one_or_none.return_value = None
        return result

    db.execute = AsyncMock(side_effect=execute_side_effect)
    return db


# ────────────────────────────────────────────────────────────────────
# Mode A: User-Direct extraction
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestUserDirectExtraction:
    """User-authenticated, no delegation headers."""

    async def test_user_direct_mode(self):
        """Basic user session → user_direct mode."""
        alice = uuid.uuid4()
        space = uuid.uuid4()
        session = make_session(user_id=alice, space_id=space)
        request = make_request()

        membership = make_membership(user_id=alice, space_id=space, role="member")
        session.db = mock_db_returns(membership)

        actor = await extract_management_actor(session, request)

        assert actor.mode == "user_direct"
        assert actor.user_id == alice
        assert actor.agent_id is None
        assert actor.space_id == space
        assert actor.space_role == "member"

    async def test_user_direct_admin_role(self):
        """Admin user → user_direct with admin role."""
        alice = uuid.uuid4()
        space = uuid.uuid4()
        session = make_session(user_id=alice, space_id=space)
        request = make_request()

        membership = make_membership(user_id=alice, space_id=space, role="admin")
        session.db = mock_db_returns(membership)

        actor = await extract_management_actor(session, request)

        assert actor.mode == "user_direct"
        assert actor.space_role == "admin"


# ────────────────────────────────────────────────────────────────────
# Mode B: Concierge Delegation extraction
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestConciergeDelegation:
    """Agent-authenticated with X-On-Behalf-Of header."""

    async def test_delegation_with_valid_header(self):
        """Concierge + X-On-Behalf-Of → concierge_delegated mode."""
        concierge_id = uuid.uuid4()
        space = uuid.uuid4()
        bob = uuid.uuid4()

        session = make_session(agent_id=concierge_id, space_id=space, is_agent=True)
        request = make_request(headers={"x-on-behalf-of": str(bob)})

        concierge_agent = make_agent_model(
            agent_id=concierge_id, management_class="concierge", home_space_id=space,
        )
        membership = make_membership(user_id=bob, space_id=space)
        bob_user = MagicMock()
        bob_user.id = bob

        # _verify_concierge_identity: agent lookup, space lookup
        # _build_delegated_actor: user lookup, membership lookup
        session.db = mock_db_returns(concierge_agent, concierge_id, bob_user, membership)

        actor = await extract_management_actor(session, request)

        assert actor.mode == "concierge_delegated"
        assert actor.user_id == bob
        assert actor.agent_id == concierge_id
        assert actor.is_space_concierge is True

    async def test_delegation_invalid_uuid_rejected(self):
        """X-On-Behalf-Of with invalid UUID → 400."""
        from fastapi import HTTPException

        concierge_id = uuid.uuid4()
        space = uuid.uuid4()

        session = make_session(agent_id=concierge_id, space_id=space, is_agent=True)
        request = make_request(headers={"x-on-behalf-of": "not-a-uuid"})

        concierge_agent = make_agent_model(
            agent_id=concierge_id, management_class="concierge", home_space_id=space,
        )
        # _verify_concierge_identity: agent lookup, space lookup
        session.db = mock_db_returns(concierge_agent, concierge_id)

        with pytest.raises(HTTPException) as exc_info:
            await extract_management_actor(session, request)
        assert exc_info.value.status_code == 400

    async def test_non_concierge_agent_rejected(self):
        """Regular agent (not concierge) trying to delegate → 403."""
        from fastapi import HTTPException

        agent_id = uuid.uuid4()
        space = uuid.uuid4()

        session = make_session(agent_id=agent_id, space_id=space, is_agent=True)
        request = make_request(headers={"x-on-behalf-of": str(uuid.uuid4())})

        # Agent is NOT a concierge
        regular_agent = make_agent_model(
            agent_id=agent_id, management_class="regular", home_space_id=space,
        )
        session.db = mock_db_returns(regular_agent)

        with pytest.raises(HTTPException) as exc_info:
            await extract_management_actor(session, request)
        assert exc_info.value.status_code == 403
        assert "concierge authority" in str(exc_info.value.detail)

    async def test_concierge_wrong_space_rejected(self):
        """Concierge from space A trying to act in space B → 403."""
        from fastapi import HTTPException

        concierge_id = uuid.uuid4()
        space_a = uuid.uuid4()
        space_b = uuid.uuid4()

        session = make_session(agent_id=concierge_id, space_id=space_b, is_agent=True)
        request = make_request(headers={"x-on-behalf-of": str(uuid.uuid4())})

        # Concierge belongs to space_a, but request is for space_b
        concierge_agent = make_agent_model(
            agent_id=concierge_id, management_class="concierge", home_space_id=space_a,
        )
        session.db = mock_db_returns(concierge_agent)

        with pytest.raises(HTTPException) as exc_info:
            await extract_management_actor(session, request)
        assert exc_info.value.status_code == 403


# ────────────────────────────────────────────────────────────────────
# Mode C: Admin Moderation extraction
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestAdminModeration:

    async def test_admin_require_admin(self):
        """User with admin role + require_admin=True → space_admin_moderation."""
        alice = uuid.uuid4()
        space = uuid.uuid4()
        session = make_session(user_id=alice, space_id=space)
        request = make_request()

        membership = make_membership(user_id=alice, space_id=space, role="admin")
        session.db = mock_db_returns(membership)

        actor = await extract_management_actor(session, request, require_admin=True)

        assert actor.mode == "space_admin_moderation"
        assert actor.space_role == "admin"

    async def test_non_admin_require_admin_rejected(self):
        """Member (not admin) + require_admin=True → 403."""
        from fastapi import HTTPException

        bob = uuid.uuid4()
        space = uuid.uuid4()
        session = make_session(user_id=bob, space_id=space)
        request = make_request()

        membership = make_membership(user_id=bob, space_id=space, role="member")
        session.db = mock_db_returns(membership)

        with pytest.raises(HTTPException) as exc_info:
            await extract_management_actor(session, request, require_admin=True)
        assert exc_info.value.status_code == 403


# ────────────────────────────────────────────────────────────────────
# Mode D: Safeguard extraction
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSafeguardExtraction:

    async def test_agent_no_delegation_header(self):
        """Concierge agent without X-On-Behalf-Of → safeguard mode."""
        concierge_id = uuid.uuid4()
        space = uuid.uuid4()

        session = make_session(agent_id=concierge_id, space_id=space, is_agent=True)
        request = make_request()  # No X-On-Behalf-Of

        concierge_agent = make_agent_model(
            agent_id=concierge_id, management_class="concierge", home_space_id=space,
        )
        # _verify_concierge_identity: agent lookup, space lookup (returns matching id)
        session.db = mock_db_returns(concierge_agent, concierge_id)

        actor = await extract_management_actor(session, request)

        assert actor.mode == "concierge_safeguard"
        assert actor.user_id is None
        assert actor.agent_id == concierge_id
        assert actor.is_space_concierge is True


# ────────────────────────────────────────────────────────────────────
# Edge cases
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestEdgeCases:

    async def test_no_user_no_agent_rejected(self):
        """Session with no user and not an agent → 401."""
        from fastapi import HTTPException

        session = MagicMock()
        session.is_agent = False
        session.agent_id = None
        session.user = None
        session.space_id = str(uuid.uuid4())
        session.db = AsyncMock()

        request = make_request()

        with pytest.raises(HTTPException) as exc_info:
            await extract_management_actor(session, request)
        assert exc_info.value.status_code == 401
