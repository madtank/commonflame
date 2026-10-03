import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core.agent_resolver import resolve_agent
from app.core.credential_service import authenticate_credential
from app.core.jwt_verify import _enforce_agent_runtime_access
from app.services.agent_control_service import AgentControlState


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def _run(coro):
    return asyncio.run(coro)


def test_enforce_agent_runtime_access_rejects_non_active_agent():
    agent_id = uuid.uuid4()
    space_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=_ScalarResult(
            SimpleNamespace(
                id=agent_id,
                space_id=space_id,
                status="disabled",
                name="stack",
            )
        )
    )

    with pytest.raises(HTTPException) as exc:
        _run(_enforce_agent_runtime_access(db, agent_id=agent_id, space_id=space_id))

    assert exc.value.status_code == 403
    assert exc.value.detail == "Agent is not active"


def test_enforce_agent_runtime_access_rejects_killswitched_agent():
    agent_id = uuid.uuid4()
    space_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=_ScalarResult(
            SimpleNamespace(
                id=agent_id,
                space_id=space_id,
                status="active",
                name="stack",
            )
        )
    )

    with patch(
        "app.core.jwt_verify.agent_control_service.get_control_state",
        new=AsyncMock(return_value=AgentControlState(is_disabled=True, disabled_reason="Emergency stop")),
    ):
        with pytest.raises(HTTPException) as exc:
            _run(_enforce_agent_runtime_access(db, agent_id=agent_id, space_id=space_id))

    assert exc.value.status_code == 403
    assert exc.value.detail == "Emergency stop"


def test_resolve_agent_rejects_killswitched_agent():
    user_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    space_id = uuid.uuid4()
    user = SimpleNamespace(
        id=user_id,
        space_id=space_id,
        _agent_id=agent_id,
        _agent_name="stack",
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=_ScalarResult(
            SimpleNamespace(
                id=agent_id,
                user_id=user_id,
                space_id=space_id,
                status="active",
                name="stack",
            )
        )
    )

    with patch(
        "app.core.agent_resolver.agent_control_service.get_control_state",
        new=AsyncMock(return_value=AgentControlState(is_disabled=True, disabled_reason="Disabled by admin")),
    ):
        with pytest.raises(HTTPException) as exc:
            _run(resolve_agent(user, db))

    assert exc.value.status_code == 403
    assert exc.value.detail == "Disabled by admin"


def test_authenticate_credential_rejects_killswitched_bound_agent():
    principal_id = uuid.uuid4()
    credential_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    space_id = uuid.uuid4()

    cred = SimpleNamespace(
        key_id="AbCd12",
        revoked_at=None,
        expires_at=None,
        secret_hash="hashed-secret",
        principal_id=principal_id,
        bound_agent_id=agent_id,
        principal_type="user",
        space_id=space_id,
        id=credential_id,
        scopes=["api:read", "api:write"],
        agent_scope="agents",
        allowed_agent_ids=None,
    )
    user = SimpleNamespace(id=principal_id, active=True, email="approved@example.com", github_id="42")
    agent = SimpleNamespace(
        id=agent_id,
        space_id=space_id,
        status="active",
        name="stack",
    )

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            None,
            _ScalarResult(cred),
            None,
            _ScalarResult(user),
            _ScalarResult(agent),
        ]
    )

    with patch("app.core.credential_service.verify_credential_secret", return_value=True):
        with patch("app.core.credential_service._credential_principal_is_access_approved", new=AsyncMock(return_value=True)):
            with patch(
                "app.services.agent_control_service.AgentControlService.get_control_state",
                new=AsyncMock(return_value=AgentControlState(is_disabled=True, disabled_reason="Emergency stop")),
            ):
                with pytest.raises(HTTPException) as exc:
                    _run(authenticate_credential("axp_u_AbCd12.secret-value", db))

    assert exc.value.status_code == 401
    assert exc.value.detail == "Emergency stop"
