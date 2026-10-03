"""
Space Validation Service
Enforces space classification rules and membership constraints
"""
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from fastapi import HTTPException, status

from ..models.space import Space
from ..models.space_membership import SpaceMembership


class SpaceValidationService:
    """Service for validating space rules and constraints"""

    @staticmethod
    def is_personal_workspace(space: Space) -> bool:
        if space.description and space.description.startswith("Personal workspace for"):
            return True
        return False

    @staticmethod
    async def get_member_count(db: AsyncSession, space_id: str) -> int:
        result = await db.execute(
            select(func.count(SpaceMembership.id))
            .where(SpaceMembership.space_id == space_id)
        )
        return result.scalar() or 0

    @staticmethod
    async def validate_member_addition(
        db: AsyncSession,
        space: Space,
        new_user_id: Optional[str] = None
    ) -> None:
        if SpaceValidationService.is_personal_workspace(space):
            member_count = await SpaceValidationService.get_member_count(db, str(space.id))
            if member_count >= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Personal workspaces cannot have additional members. Create a team workspace to collaborate with others."
                )

        if space.visibility == "private" and not SpaceValidationService.is_personal_workspace(space):
            member_count = await SpaceValidationService.get_member_count(db, str(space.id))
            if member_count == 1:
                pass

    @staticmethod
    async def validate_join_request(
        db: AsyncSession,
        space: Space,
        user_id: str
    ) -> None:
        if SpaceValidationService.is_personal_workspace(space):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot join personal workspaces. This workspace is private to its owner."
            )

        existing_membership = await db.execute(
            select(SpaceMembership)
            .where(
                SpaceMembership.space_id == space.id,
                SpaceMembership.user_id == user_id
            )
        )
        if existing_membership.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User is already a member of this space"
            )

    @staticmethod
    def get_workspace_type(space: Space, member_count: Optional[int] = None) -> str:
        if SpaceValidationService.is_personal_workspace(space):
            return "personal"
        if space.visibility == "public":
            return "public"
        if space.visibility == "private" and member_count == 1:
            return "team"
        return "team"

    @staticmethod
    async def validate_space_update(
        db: AsyncSession,
        space: Space,
        updates: dict
    ) -> None:
        if SpaceValidationService.is_personal_workspace(space):
            if updates.get("visibility") == "public":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Personal workspaces cannot be made public"
                )

        if "description" in updates and SpaceValidationService.is_personal_workspace(space):
            new_description = updates["description"]
            if not new_description or not new_description.startswith("Personal workspace for"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Personal workspace description must maintain the format 'Personal workspace for [name]'"
                )

    @staticmethod
    async def enforce_personal_workspace_limits(db: AsyncSession) -> dict:
        violations = []

        result = await db.execute(
            select(Space)
            .where(Space.description.like("Personal workspace for%"))
        )
        personal_spaces = result.scalars().all()

        for space in personal_spaces:
            member_count = await SpaceValidationService.get_member_count(db, str(space.id))
            if member_count > 1:
                violations.append({
                    "space_id": str(space.id),
                    "space_name": space.name,
                    "member_count": member_count,
                    "violation": "Personal workspace has multiple members"
                })

        return {
            "checked": len(personal_spaces),
            "violations": violations,
            "passed": len(violations) == 0
        }


# Backward compatibility alias
OrganizationValidationService = SpaceValidationService
