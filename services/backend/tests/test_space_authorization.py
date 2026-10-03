"""
Space Authorization Hardening Tests

Verifies defense-in-depth layers:
1. verify_space_membership() rejects non-members
2. _resolve_space_id() enforces membership on caller-supplied space_id
3. No f-string SET LOCAL remaining (SQL injection regression)
4. /agents/health is space-scoped
"""

import os
import re
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = [pytest.mark.security, pytest.mark.unit]


# ---------------------------------------------------------------------------
# 1. verify_space_membership unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_space_membership_rejects_non_member():
    """verify_space_membership raises 403 for non-members."""
    from app.core.authorization import verify_space_membership

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=False)

    user_id = uuid.uuid4()
    space_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        await verify_space_membership(db, user_id, space_id)

    assert exc_info.value.status_code == 403
    assert "Not a member" in exc_info.value.detail


@pytest.mark.asyncio
async def test_verify_space_membership_allows_member():
    """verify_space_membership passes silently for members."""
    from app.core.authorization import verify_space_membership

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=True)

    user_id = uuid.uuid4()
    space_id = uuid.uuid4()

    # Should not raise
    await verify_space_membership(db, user_id, space_id)


@pytest.mark.asyncio
async def test_verify_space_membership_accepts_string_space_id():
    """verify_space_membership accepts string space_id and converts to UUID."""
    from app.core.authorization import verify_space_membership

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=True)

    user_id = uuid.uuid4()
    space_id = str(uuid.uuid4())

    await verify_space_membership(db, user_id, space_id)


@pytest.mark.asyncio
async def test_verify_space_actor_access_delegates_user_principals_to_membership():
    """User principals still require human space membership."""
    from app.core.authorization import verify_space_actor_access

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=True)

    await verify_space_actor_access(
        db,
        user_id=uuid.uuid4(),
        space_id=uuid.uuid4(),
        is_agent=False,
    )

    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_space_actor_access_allows_active_agent_attachment():
    """Agent principals can operate in spaces with active AgentSpaceAccess."""
    from app.core.authorization import verify_space_actor_access

    db = AsyncMock()
    db.scalar = AsyncMock(return_value=True)

    await verify_space_actor_access(
        db,
        user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        space_id=uuid.uuid4(),
        is_agent=True,
    )

    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_space_actor_access_allows_registered_space_agent():
    """Registered space agents are allowed even if the access row is missing."""
    from app.core.authorization import verify_space_actor_access

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[False, True])

    await verify_space_actor_access(
        db,
        user_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        space_id=uuid.uuid4(),
        is_agent=True,
    )

    assert db.scalar.await_count == 2


@pytest.mark.asyncio
async def test_verify_space_actor_access_rejects_unattached_agent():
    """Agent principals cannot access unrelated spaces."""
    from app.core.authorization import verify_space_actor_access

    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[False, False])

    with pytest.raises(HTTPException) as exc_info:
        await verify_space_actor_access(
            db,
            user_id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            space_id=uuid.uuid4(),
            is_agent=True,
        )

    assert exc_info.value.status_code == 403
    assert "Agent is not attached" in exc_info.value.detail


# ---------------------------------------------------------------------------
# 2. _resolve_space_id membership enforcement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_space_id_rejects_non_member_space():
    """_resolve_space_id raises 403 when caller provides a non-member space_id."""
    from app.api.v1.api_v1 import _resolve_space_id

    user = MagicMock()
    user.id = uuid.uuid4()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=False)

    foreign_space = str(uuid.uuid4())

    with pytest.raises(HTTPException) as exc_info:
        await _resolve_space_id(user, foreign_space, db)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_space_id_allows_member_space():
    """_resolve_space_id returns space_id when user is a member."""
    from app.api.v1.api_v1 import _resolve_space_id

    user = MagicMock()
    user.id = uuid.uuid4()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=True)

    target_space = str(uuid.uuid4())
    result = await _resolve_space_id(user, target_space, db)
    assert result == target_space


@pytest.mark.asyncio
async def test_resolve_space_id_allows_agent_actor_space():
    """_resolve_space_id accepts agent-space access for agent principals."""
    from app.api.v1.api_v1 import _resolve_space_id

    user = MagicMock()
    user.id = uuid.uuid4()
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=True)
    session = MagicMock()
    session.is_agent = True
    session.agent_id = uuid.uuid4()

    target_space = str(uuid.uuid4())
    user._effective_space_id = target_space
    session.space_id = target_space
    result = await _resolve_space_id(user, target_space, db, session=session)
    assert result == target_space
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_space_id_falls_back_without_explicit_space():
    """_resolve_space_id uses _effective_space_id when no space_id provided."""
    from app.api.v1.api_v1 import _resolve_space_id

    user = MagicMock()
    user.id = uuid.uuid4()
    default_space = str(uuid.uuid4())

    db = AsyncMock()

    with patch("app.api.v1.api_v1._effective_space_id", return_value=default_space):
        result = await _resolve_space_id(user, None, db)

    assert result == default_space
    # verify_space_membership should NOT have been called (no explicit space_id)
    db.scalar.assert_not_called()


# ---------------------------------------------------------------------------
# 3. SQL injection regression — no f-string SET LOCAL
# ---------------------------------------------------------------------------

def test_no_fstring_set_local_in_codebase():
    """Ensure no f-string SET LOCAL patterns remain in app/ code."""
    app_dir = Path(__file__).parent.parent / "app"
    pattern = re.compile(r"""f["']SET\s+LOCAL""", re.IGNORECASE)

    violations = []
    for py_file in app_dir.rglob("*.py"):
        content = py_file.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if pattern.search(line):
                violations.append(f"{py_file}:{i}: {line.strip()}")

    assert violations == [], (
        f"Found f-string SET LOCAL (SQL injection risk):\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# 4. message_summaries uses message.space_id
# ---------------------------------------------------------------------------

def test_message_summaries_uses_message_space_id():
    """Verify message_summaries.py uses message.space_id, not current_space_id."""
    src = (Path(__file__).parent.parent / "app/api/v1/message_summaries.py").read_text()

    # Should NOT contain current_space_id in authorization context
    # (comments are OK, actual code usage is not)
    lines = src.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "session.user.current_space_id" not in stripped, (
            f"message_summaries.py:{i} still uses stale current_space_id: {stripped}"
        )


# ---------------------------------------------------------------------------
# 5. agents.py and notifications.py don't use current_space_id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", [
    "app/api/v1/agents.py",
    "app/api/v1/notifications.py",
    "app/api/v1/spaces_intelligence.py",
])
def test_no_stale_current_space_id_in_auth_files(filename):
    """Files that were migrated should not reference session.user.current_space_id."""
    src = (Path(__file__).parent.parent / filename).read_text()

    violations = []
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # Allow the string "current_space_id" in variable names that were renamed,
        # log messages, and set_config calls (which reference the PG setting name)
        if "current_space_id" in stripped:
            # Whitelist: set_config uses the PG setting name 'app.current_space_id'
            if "app.current_space_id" in stripped:
                continue
            # Whitelist: variable assignments FROM session.space_id
            if "= session.space_id" in stripped:
                continue
            # Flag: actual usage of session.user.current_space_id
            if "session.user.current_space_id" in stripped:
                violations.append(f"{filename}:{i}: {stripped}")
            # Flag: getattr(session.user, "current_space_id"
            if 'getattr(session.user, "current_space_id"' in stripped:
                violations.append(f"{filename}:{i}: {stripped}")

    assert violations == [], (
        f"Stale current_space_id usage found:\n" + "\n".join(violations)
    )


def test_v1_get_space_returns_mcp_scope_metadata():
    """GET /api/v1/spaces/{space_id} must expose stable scope fields for MCP."""
    src = (Path(__file__).parent.parent / "app/api/v1/api_v1.py").read_text()
    route_start = src.index('@router.get("/spaces/{space_id}")')
    route_end = src.index('@router.get("/spaces/{space_id}/members")')
    route_src = src[route_start:route_end]

    assert '"visibility": org.visibility' in route_src
    assert '"is_personal": str(org.id) == str(user.space_id)' in route_src
    assert '"space_mode":' in route_src
    assert '"viewer_role": membership.role if membership else None' in route_src
    assert '"role": membership.role if membership else None' in route_src
    assert '"member_role": membership.role if membership else None' in route_src


def test_v1_list_spaces_returns_mcp_scope_metadata():
    """GET /api/v1/spaces must expose stable scope fields for MCP."""
    src = (Path(__file__).parent.parent / "app/api/v1/api_v1.py").read_text()
    route_start = src.index('@router.get("/spaces")')
    route_end = src.index('@router.get("/spaces/{space_id}")')
    route_src = src[route_start:route_end]

    assert '"visibility": org.visibility' in route_src
    assert '"is_member": True' in route_src
    assert '"is_current": str(org.id)' in route_src
    assert '"is_personal": str(org.id) == str(user.space_id)' in route_src
    assert '"space_mode":' in route_src
    assert '"viewer_role": role' in route_src
    assert '"role": role' in route_src
    assert '"member_role": role' in route_src
