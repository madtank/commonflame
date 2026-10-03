from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.security import revoke_refresh_token


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


@pytest.mark.asyncio
async def test_revoke_refresh_token_resets_current_space_to_home():
    user_id = uuid4()
    home_space = uuid4()
    sticky_space = uuid4()
    db_token = SimpleNamespace(user_id=user_id, revoked_at=None)
    user = SimpleNamespace(id=user_id, space_id=home_space, current_space_id=sticky_space)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_ScalarResult(db_token), _ScalarResult(user)])
    db.commit = AsyncMock()

    revoked = await revoke_refresh_token(db, "refresh-token")

    assert revoked is True
    assert db_token.revoked_at is not None
    assert user.current_space_id == home_space
    assert db.execute.await_count == 2
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_refresh_token_leaves_home_space_unchanged():
    user_id = uuid4()
    home_space = uuid4()
    db_token = SimpleNamespace(user_id=user_id, revoked_at=None)
    user = SimpleNamespace(id=user_id, space_id=home_space, current_space_id=home_space)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_ScalarResult(db_token), _ScalarResult(user)])
    db.commit = AsyncMock()

    revoked = await revoke_refresh_token(db, "refresh-token")

    assert revoked is True
    assert user.current_space_id == home_space
    db.commit.assert_awaited_once()
