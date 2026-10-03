"""
Feature Flags API — GET /api/v1/flags, PUT /api/v1/flags/{name}

Returns merged feature flags for the authenticated user (global → org → user).
Uses SecureSession for RLS-ready auth + org scoping.
"""

import logging
from typing import Dict

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, or_, and_

from ...core.rls import SecureSessionDep
from ...models.feature_flag import FeatureFlag

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/flags", tags=["feature-flags"])


class FlagsResponse(BaseModel):
    flags: Dict[str, bool]


@router.get("/", response_model=FlagsResponse)
async def get_flags(session: SecureSessionDep):
    """
    Return merged feature flags for the current user.

    Priority (highest wins):
    1. User-specific flag (user_id + space_id)
    2. Org-wide flag (space_id, user_id = NULL)
    3. Global flag (space_id = NULL, user_id = NULL)
    """
    db = session.db
    space_id = session.space_id
    user_id = session.user.id

    result = await db.execute(
        select(FeatureFlag).where(
            or_(
                # Global flags
                and_(FeatureFlag.space_id.is_(None), FeatureFlag.user_id.is_(None)),
                # Org-wide flags
                and_(FeatureFlag.space_id == space_id, FeatureFlag.user_id.is_(None)),
                # User-specific flags
                and_(FeatureFlag.space_id == space_id, FeatureFlag.user_id == user_id),
            )
        )
    )
    rows = result.scalars().all()

    # Merge: global → org → user (last write wins by specificity)
    flags: Dict[str, bool] = {}
    for row in sorted(rows, key=lambda r: (r.space_id is not None, r.user_id is not None)):
        flags[row.flag_name] = row.enabled

    return FlagsResponse(flags=flags)


@router.put("/{flag_name}")
async def set_flag(
    flag_name: str,
    enabled: bool = True,
    scope: str = "user",  # "global", "org", or "user"
    session: SecureSessionDep = None,
):
    """Set a feature flag. Scope: global, org, or user."""
    db = session.db
    space_id = session.space_id
    user_id = session.user.id

    if scope == "global":
        # TODO: admin-only guard once roles are wired up
        target_space_id = None
        target_user_id = None
    elif scope == "org":
        target_space_id = space_id
        target_user_id = None
    else:  # user
        target_space_id = space_id
        target_user_id = user_id

    # Upsert
    existing = await db.execute(
        select(FeatureFlag).where(
            FeatureFlag.flag_name == flag_name,
            FeatureFlag.space_id == target_space_id if target_space_id else FeatureFlag.space_id.is_(None),
            FeatureFlag.user_id == target_user_id if target_user_id else FeatureFlag.user_id.is_(None),
        )
    )
    flag = existing.scalar_one_or_none()

    if flag:
        flag.enabled = enabled
    else:
        flag = FeatureFlag(
            flag_name=flag_name,
            space_id=target_space_id,
            user_id=target_user_id,
            enabled=enabled,
        )
        db.add(flag)

    await db.commit()
    return {"flag_name": flag_name, "enabled": enabled, "scope": scope}


@router.delete("/{flag_name}")
async def delete_flag(
    flag_name: str,
    scope: str = "user",
    session: SecureSessionDep = None,
):
    """Remove a feature flag override."""
    db = session.db
    space_id = session.space_id
    user_id = session.user.id

    if scope == "global":
        target_space_id = None
        target_user_id = None
    elif scope == "org":
        target_space_id = space_id
        target_user_id = None
    else:
        target_space_id = space_id
        target_user_id = user_id

    existing = await db.execute(
        select(FeatureFlag).where(
            FeatureFlag.flag_name == flag_name,
            FeatureFlag.space_id == target_space_id if target_space_id else FeatureFlag.space_id.is_(None),
            FeatureFlag.user_id == target_user_id if target_user_id else FeatureFlag.user_id.is_(None),
        )
    )
    flag = existing.scalar_one_or_none()
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")

    await db.delete(flag)
    await db.commit()
    return {"deleted": flag_name, "scope": scope}
