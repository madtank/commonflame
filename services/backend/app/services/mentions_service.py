"""
Mentions Service
Handles parsing, storing, and notifying agents of @mentions in messages
"""

import re
import logging
from typing import List, Set, Dict, Any, Optional
from uuid import UUID
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, update

from ..models.mention import Mention
from ..models.agent import Agent
from ..models.message import Message

logger = logging.getLogger(__name__)


class MentionsService:
    """Service for handling @mentions in messages"""

    # Regex pattern for extracting @mentions from message content
    # Matches @username or @agent-name (alphanumeric, underscore, hyphen)
    # Uses negative lookbehind to exclude alphanumeric chars before @ (avoids emails)
    # This allows @mentions after ANY punctuation/markdown: ~~@agent~~, >@agent, etc.
    MENTION_PATTERN = re.compile(r'(?<![a-zA-Z0-9])@([\w-]+)(?=\s|$|[^\w-])')

    # Patterns for stripping code blocks/inline code before mention extraction
    _CODE_FENCE_PATTERN = re.compile(r'```[\s\S]*?```', re.MULTILINE)
    _INLINE_CODE_PATTERN = re.compile(r'`[^`]+`')

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def verify_agent_ownership(self, user_id: UUID, agent_id: UUID) -> bool:
        """
        Verify that an agent belongs to a specific user.

        Args:
            user_id: User ID to check
            agent_id: Agent ID to verify

        Returns:
            True if agent belongs to user, False otherwise
        """
        try:
            result = await self.db.execute(
                select(Agent).where(
                    and_(
                        Agent.id == agent_id,
                        Agent.user_id == user_id
                    )
                )
            )
            agent = result.scalar_one_or_none()
            return agent is not None
        except Exception as e:
            logger.error(f"Error verifying agent ownership: {e}")
            return False

    @classmethod
    def parse_mentions(cls, content: str) -> Set[str]:
        """
        Extract all @mentions from message content.

        Args:
            content: Message text to parse

        Returns:
            Set of mentioned agent names (without @ prefix)
        """
        if not content:
            return set()

        # Strip code fences and inline code before parsing mentions
        # so @mentions inside code blocks are not treated as real mentions
        stripped = cls._CODE_FENCE_PATTERN.sub('', content)
        stripped = cls._INLINE_CODE_PATTERN.sub('', stripped)

        mentions = cls.MENTION_PATTERN.findall(stripped)
        # Normalize to lowercase and deduplicate
        return {m.lower() for m in mentions if m}

    async def get_agent_by_name(self, agent_name: str, space_id: UUID) -> Optional[Agent]:
        """
        Find an agent by name within an organization.

        Args:
            agent_name: Name of the agent (case-insensitive)
            space_id: Organization ID to search within

        Returns:
            Agent object if found, None otherwise
        """
        result = await self.db.execute(
            select(Agent).where(
                and_(
                    Agent.name.ilike(agent_name),  # Case-insensitive
                    Agent.space_id == space_id
                )
            )
        )
        return result.scalar_one_or_none()

    async def create_mention(
        self,
        message_id: UUID,
        mentioned_agent_id: UUID,
        mentioning_agent_id: Optional[UUID],
        space_id: UUID
    ) -> Optional[Mention]:
        """
        Create a new mention record with proper error handling.

        Args:
            message_id: ID of the message containing the mention
            mentioned_agent_id: ID of the agent being mentioned
            mentioning_agent_id: ID of the agent who mentioned (can be None for user mentions)
            space_id: Organization ID

        Returns:
            Created Mention object, or None if creation failed
        """
        try:
            # Verify mentioned agent exists
            result = await self.db.execute(
                select(Agent).where(Agent.id == mentioned_agent_id)
            )
            mentioned_agent = result.scalar_one_or_none()

            if not mentioned_agent:
                logger.warning(f"Cannot create mention: agent {mentioned_agent_id} does not exist")
                return None

            # Check for duplicate mention (same message, same agent)
            existing_result = await self.db.execute(
                select(Mention).where(
                    and_(
                        Mention.message_id == message_id,
                        Mention.mentioned_agent_id == mentioned_agent_id
                    )
                )
            )
            existing_mention = existing_result.scalar_one_or_none()
            if existing_mention:
                logger.debug(f"Mention already exists for agent {mentioned_agent_id} in message {message_id}")
                return existing_mention

            # Create mention
            mention = Mention(
                message_id=message_id,
                mentioned_agent_id=mentioned_agent_id,
                mentioning_agent_id=mentioning_agent_id,
                space_id=space_id
            )
            self.db.add(mention)
            await self.db.flush()  # Flush to get the ID without committing
            return mention

        except Exception as e:
            logger.error(f"Error creating mention: {e}", exc_info=True)
            # Note: Transaction rollback should be handled by caller
            return None

    async def process_message_mentions(
        self,
        message_id: UUID,
        content: str,
        mentioning_agent_id: Optional[UUID],
        space_id: UUID
    ) -> List[Mention]:
        """
        Parse mentions from message content and create mention records.

        Args:
            message_id: ID of the message
            content: Message content to parse
            mentioning_agent_id: ID of agent who sent the message (None if user)
            space_id: Organization ID

        Returns:
            List of created Mention objects
        """
        mentions = []
        mentioned_agent_ids: Set[UUID] = set()

        # Phase 1: Explicit @mentions (existing behavior)
        agent_names = self.parse_mentions(content)

        if agent_names:
            logger.info(f"Found {len(agent_names)} explicit mentions in message {message_id}: {agent_names}")

        for agent_name in agent_names:
            agent = await self.get_agent_by_name(agent_name, space_id)

            if not agent:
                logger.debug(f"Agent '{agent_name}' not found in org {space_id}")
                continue

            if mentioning_agent_id and agent.id == mentioning_agent_id:
                logger.debug(f"Skipping self-mention for agent {agent_name}")
                continue

            mention = await self.create_mention(
                message_id=message_id,
                mentioned_agent_id=agent.id,
                mentioning_agent_id=mentioning_agent_id,
                space_id=space_id
            )
            if mention:
                mentions.append(mention)
                mentioned_agent_ids.add(agent.id)
                logger.info(f"Created explicit mention for agent {agent_name} (id={agent.id})")

        # Phase 2: Implicit mentions — detect agent handles referenced
        # as plain text (without @prefix). Only exact full-handle matches
        # to avoid false positives with short/common names.
        implicit_names = await self._detect_implicit_mentions(content, space_id)

        for agent_name, agent in implicit_names:
            # Skip if already explicitly mentioned
            if agent.id in mentioned_agent_ids:
                continue

            # Skip self-mentions
            if mentioning_agent_id and agent.id == mentioning_agent_id:
                continue

            mention = await self.create_mention(
                message_id=message_id,
                mentioned_agent_id=agent.id,
                mentioning_agent_id=mentioning_agent_id,
                space_id=space_id
            )
            if mention:
                mentions.append(mention)
                mentioned_agent_ids.add(agent.id)
                logger.info(f"Created implicit mention for agent {agent_name} (id={agent.id})")

        return mentions

    async def _detect_implicit_mentions(
        self,
        content: str,
        space_id: UUID
    ) -> List[tuple]:
        """
        Detect agent handles mentioned as plain text (without @ prefix).

        Only matches exact full handles (e.g. 'logic_runner_677') to avoid
        false positives. Handles must appear as whole words (bounded by
        non-alphanumeric/underscore/hyphen characters or string edges).

        Args:
            content: Message content to scan
            space_id: Organization ID to scope agent lookup

        Returns:
            List of (agent_name, Agent) tuples for implicitly mentioned agents
        """
        if not content:
            return []

        # Strip code blocks to avoid matching handles in code
        stripped = self._CODE_FENCE_PATTERN.sub('', content)
        stripped = self._INLINE_CODE_PATTERN.sub('', stripped)
        content_lower = stripped.lower()

        # Get all agents in the org
        result = await self.db.execute(
            select(Agent).where(Agent.space_id == space_id)
        )
        agents = result.scalars().all()

        implicit = []
        for agent in agents:
            name_lower = agent.name.lower()
            # Skip very short names (< 4 chars) to reduce false positives
            if len(name_lower) < 4:
                continue
            # Check for whole-word match using word boundary regex
            # Agent names use alphanumeric + underscore + hyphen
            pattern = re.compile(
                r'(?<![a-zA-Z0-9_-])' + re.escape(name_lower) + r'(?![a-zA-Z0-9_-])'
            )
            if pattern.search(content_lower):
                logger.debug(f"Implicit mention detected: '{agent.name}' in message")
                implicit.append((agent.name, agent))

        return implicit

    async def get_unread_mentions(
        self,
        agent_id: UUID,
        space_id: UUID,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get unread mentions for an agent with eager loading to prevent N+1 queries.

        Args:
            agent_id: ID of the agent
            space_id: Organization ID
            limit: Maximum number of mentions to return

        Returns:
            List of mention dictionaries with message details
        """
        from sqlalchemy.orm import selectinload

        result = await self.db.execute(
            select(Mention)
            .options(
                selectinload(Mention.message),
                selectinload(Mention.mentioning_agent)
            )
            .where(
                and_(
                    Mention.mentioned_agent_id == agent_id,
                    Mention.space_id == space_id,
                    Mention.read_at.is_(None)
                )
            )
            .order_by(Mention.created_at.desc())
            .limit(limit)
        )

        mentions = result.scalars().all()
        mentions_data = []

        for mention in mentions:
            mentions_data.append({
                "mention_id": str(mention.id),
                "message_id": str(mention.message_id),
                "content": mention.message.content if mention.message else "",
                "channel": mention.message.channel if mention.message else "unknown",
                "mentioning_agent": mention.mentioning_agent.name if mention.mentioning_agent else "user",
                "created_at": mention.created_at.isoformat(),
                "message_created_at": mention.message.created_at.isoformat() if mention.message else mention.created_at.isoformat()
            })

        return mentions_data

    async def mark_mentions_as_read(
        self,
        mention_ids: List[UUID],
        agent_id: UUID
    ) -> int:
        """
        Mark mentions as read for an agent.

        Args:
            mention_ids: List of mention IDs to mark as read
            agent_id: ID of the agent (for security check)

        Returns:
            Number of mentions marked as read
        """
        if not mention_ids:
            return 0

        result = await self.db.execute(
            update(Mention)
            .where(
                and_(
                    Mention.id.in_(mention_ids),
                    Mention.mentioned_agent_id == agent_id,
                    Mention.read_at.is_(None)
                )
            )
            .values(read_at=datetime.utcnow())
        )

        return result.rowcount

    async def get_mention_count(
        self,
        agent_id: UUID,
        space_id: UUID,
        unread_only: bool = True
    ) -> int:
        """
        Get count of mentions for an agent.

        Args:
            agent_id: ID of the agent
            space_id: Organization ID
            unread_only: If True, count only unread mentions

        Returns:
            Count of mentions
        """
        from sqlalchemy import func

        conditions = [
            Mention.mentioned_agent_id == agent_id,
            Mention.space_id == space_id
        ]

        if unread_only:
            conditions.append(Mention.read_at.is_(None))

        result = await self.db.execute(
            select(func.count(Mention.id))
            .where(and_(*conditions))
        )

        return result.scalar() or 0
