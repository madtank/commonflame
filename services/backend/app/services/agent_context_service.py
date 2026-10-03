"""
Agent Context Service
Handles user-portable agent queries with proper org context isolation
"""

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models.agent import Agent
from ..models.space_membership import SpaceMembership


class AgentContextService:
    """Service for managing agent visibility in user's current org context"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def get_agents_for_user_context(
        self, user_id: str, current_space_id: str, include_private: bool = True
    ) -> list[Agent]:
        """
        Get all agents visible to user in their current org context

        Args:
            user_id: Current user's ID
            current_space_id: User's current active organization
            include_private: Whether to include user's private agents

        Returns:
            List of agents visible in current context
        """

        # Get all users in the current org
        org_users_query = select(SpaceMembership.user_id).where(SpaceMembership.space_id == current_space_id)
        org_users_result = await self.db.execute(org_users_query)
        org_user_ids = [row[0] for row in org_users_result.fetchall()]

        # Build agent visibility query
        visibility_conditions = []

        # 1. Org-visible and public agents from org members
        if org_user_ids:
            visibility_conditions.append(
                and_(Agent.user_id.in_(org_user_ids), Agent.visibility_level.in_(["org_visible", "public"]))
            )

        # 2. User's private agents (if requested)
        if include_private:
            visibility_conditions.append(and_(Agent.user_id == user_id, Agent.visibility_level == "private"))

        # 3. Public agents from outside the org (marketplace discovery)
        visibility_conditions.append(
            and_(Agent.user_id.notin_(org_user_ids) if org_user_ids else True, Agent.visibility_level == "public")
        )

        # Execute query
        if not visibility_conditions:
            return []

        query = (
            select(Agent)
            .where(or_(*visibility_conditions))
            .where(Agent.is_internal.is_(False))  # Exclude internal system agents
            .options(selectinload(Agent.user))
            .order_by(Agent.created_at.desc())
        )

        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_user_agents(self, user_id: str) -> list[Agent]:
        """Get all agents owned by a specific user"""
        query = (
            select(Agent)
            .where(Agent.user_id == user_id)
            .where(Agent.is_internal.is_(False))  # Exclude internal system agents
            .options(selectinload(Agent.user))
            .order_by(Agent.created_at.desc())
        )
        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_org_member_agents(self, current_space_id: str, exclude_user_id: str | None = None) -> list[Agent]:
        """
        Get agents from other org members (useful for showing 'team capabilities')

        Args:
            current_space_id: Organization to get agents from
            exclude_user_id: Exclude agents from this user (e.g., current user)
        """
        # Get org member user IDs
        org_users_query = select(SpaceMembership.user_id).where(SpaceMembership.space_id == current_space_id)

        if exclude_user_id:
            org_users_query = org_users_query.where(SpaceMembership.user_id != exclude_user_id)

        org_users_result = await self.db.execute(org_users_query)
        org_user_ids = [row[0] for row in org_users_result.fetchall()]

        if not org_user_ids:
            return []

        # Get org-visible agents from these users
        query = (
            select(Agent)
            .where(and_(Agent.user_id.in_(org_user_ids), Agent.visibility_level.in_(["org_visible", "public"])))
            .where(Agent.is_internal.is_(False))  # Exclude internal system agents
            .options(selectinload(Agent.user))
            .order_by(Agent.created_at.desc())
        )

        result = await self.db.execute(query)
        return result.scalars().all()

    async def can_user_access_agent(self, user_id: str, agent_id: str, current_space_id: str) -> bool:
        """
        Check if user can access a specific agent in their current context

        Args:
            user_id: User requesting access
            agent_id: Agent being accessed
            current_space_id: User's current org context

        Returns:
            True if user can access agent, False otherwise
        """
        # Get the agent
        agent_query = select(Agent).where(Agent.id == agent_id)
        agent_result = await self.db.execute(agent_query)
        agent = agent_result.scalar_one_or_none()

        if not agent:
            return False

        # User always has access to their own agents
        if agent.user_id == user_id:
            return True

        # Public agents are accessible to everyone
        if agent.visibility_level == "public":
            return True

        # For org_visible agents, check if agent owner is in same org
        if agent.visibility_level == "org_visible":
            # Check if agent owner is member of user's current org
            membership_query = select(SpaceMembership).where(
                and_(SpaceMembership.user_id == agent.user_id, SpaceMembership.space_id == current_space_id)
            )
            membership_result = await self.db.execute(membership_query)
            return membership_result.scalar_one_or_none() is not None

        # Private agents are only accessible to owner
        return False

    async def get_new_team_agents_for_user(self, user_id: str, current_space_id: str) -> list[Agent]:
        """
        Get agents that are newly available to user in current org
        (useful for showing 'new team capabilities' when user joins org)
        """
        return await self.get_org_member_agents(current_space_id, exclude_user_id=user_id)
