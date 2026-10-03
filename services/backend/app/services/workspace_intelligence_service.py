"""
Workspace Intelligence Service - The Core Memory Engine

Provides tiered persistence for agent artifacts:
- Ephemeral: Redis (with TTL)
- Permanent: Postgres workspace_intelligence table (The Vault)

Key Features:
- Atomic upsert with version incrementing
- Automatic history archiving on updates
- Summary snippet auto-generation
- Artifact type classification for Sentinel filtering
- Access logging for popularity tracking
- Multi-tenant isolation via space_id
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, delete, select, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import redis_client
from app.models.workspace_intelligence import (
    ArtifactType,
    WorkspaceIntelligence,
    WorkspaceIntelligenceHistory,
)

logger = logging.getLogger(__name__)

# Maximum length for summary snippets
SUMMARY_SNIPPET_MAX_LENGTH = 200


def generate_summary_snippet(payload: dict | Any, max_length: int = SUMMARY_SNIPPET_MAX_LENGTH) -> str:
    """
    Auto-generate a summary snippet from the payload for fast UI previews.

    Extraction priority:
    1. Explicit 'summary' field
    2. 'description' field
    3. 'content' field
    4. 'query' + first result
    5. JSON stringification (truncated)
    """
    if not payload:
        return ""

    if isinstance(payload, str):
        return payload[:max_length].strip()

    if not isinstance(payload, dict):
        try:
            text = json.dumps(payload)
            return text[:max_length].strip()
        except (TypeError, ValueError):
            return str(payload)[:max_length].strip()

    # Priority 1: Explicit summary
    if "summary" in payload and payload["summary"]:
        return str(payload["summary"])[:max_length].strip()

    # Priority 2: Description
    if "description" in payload and payload["description"]:
        return str(payload["description"])[:max_length].strip()

    # Priority 3: Content
    if "content" in payload and payload["content"]:
        content = str(payload["content"])
        # Strip HTML/markdown if present (with exception handling for malformed input)
        try:
            content = re.sub(r'<[^>]+>', '', content)
            content = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', content)
        except re.error:
            pass  # Use content as-is if regex fails
        return content[:max_length].strip()

    # Priority 4: Query + first result
    if "query" in payload:
        query = str(payload["query"])
        if "results" in payload and isinstance(payload["results"], list) and payload["results"]:
            first_result = payload["results"][0]
            if isinstance(first_result, dict):
                result_text = first_result.get("title", first_result.get("name", ""))
            else:
                result_text = str(first_result)
            snippet = f"Query: {query} → {result_text}"
            return snippet[:max_length].strip()
        return f"Query: {query}"[:max_length].strip()

    # Priority 5: JSON stringification
    try:
        text = json.dumps(payload, default=str)
        return text[:max_length].strip()
    except (TypeError, ValueError):
        return str(payload)[:max_length].strip()


def infer_artifact_type(key: str, payload: dict | Any) -> ArtifactType:
    """
    Infer artifact type from key prefix or payload structure.

    Key prefixes:
    - research:* → RESEARCH
    - insight:*, conversation:* → CONVERSATION_INSIGHT
    - task:*, state:* → TASK_STATE
    - validation:*, security:*, health:* → SYSTEM_VALIDATION
    """
    key_lower = key.lower()

    if key_lower.startswith(("research:", "search:", "web:", "news:", "docs:")):
        return ArtifactType.RESEARCH
    if key_lower.startswith(("insight:", "conversation:", "chat:", "discussion:")):
        return ArtifactType.CONVERSATION_INSIGHT
    if key_lower.startswith(("task:", "state:", "progress:", "decision:")):
        return ArtifactType.TASK_STATE
    if key_lower.startswith(("validation:", "security:", "health:", "audit:", "compliance:")):
        return ArtifactType.SYSTEM_VALIDATION

    # Infer from payload structure
    if isinstance(payload, dict):
        if "query" in payload or "search_results" in payload or "raw_links" in payload:
            return ArtifactType.RESEARCH
        if "task_id" in payload or "status" in payload or "progress" in payload:
            return ArtifactType.TASK_STATE
        if "validation_result" in payload or "security_score" in payload:
            return ArtifactType.SYSTEM_VALIDATION

    # Default to RESEARCH
    return ArtifactType.RESEARCH


class WorkspaceIntelligenceService:
    """
    Service for managing the Workspace Intelligence Vault.

    Implements the "Living Artifact" model with versioning and history.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.redis = redis_client

    async def promote_intelligence(
        self,
        space_id: UUID,
        key: str,
        agent_id: str | None = None,
        artifact_type: ArtifactType | str | None = None,
    ) -> dict[str, Any]:
        """
        Promote ephemeral Redis context to permanent Workspace Intelligence.

        Implements atomic upsert with version incrementing:
        - If key exists: Archive current version to history, increment version
        - If new: Create with version 1

        Args:
            space_id: The workspace UUID (strict multi-tenant isolation)
            key: The semantic lookup key (e.g., 'research:mcp-oauth:2026-01-01')
            agent_id: Optional agent handle override
            artifact_type: Optional artifact type override

        Returns:
            Dict with status, permanent UUID, version, and access_url

        Raises:
            ValueError: If key not found in Redis or invalid schema
        """
        # Build the full Redis key
        redis_key = f"context:{space_id}:{key}"

        # Fetch from Redis
        raw_value = await self.redis.get(redis_key)
        if raw_value is None:
            raise ValueError(f"Context key not found in Redis: {key}")

        # Parse and validate
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in Redis context: {e}")

        if not isinstance(parsed, dict):
            raise ValueError("Context value must be a JSON object")

        # Extract payload (the actual data)
        payload = parsed.get("value", parsed)

        # Resolve agent_id
        resolved_agent_id = agent_id or parsed.get("agent_name", "unknown")

        # Resolve artifact type
        if artifact_type:
            if isinstance(artifact_type, str):
                try:
                    resolved_type = ArtifactType(artifact_type)
                except ValueError:
                    resolved_type = infer_artifact_type(key, payload)
            else:
                resolved_type = artifact_type
        else:
            resolved_type = infer_artifact_type(key, payload)

        # Generate summary snippet
        summary = None
        if isinstance(parsed, dict):
            explicit_summary = parsed.get("summary") or parsed.get("summary_snippet")
            if isinstance(explicit_summary, str) and explicit_summary.strip():
                summary = explicit_summary.strip()
        if not summary:
            summary = generate_summary_snippet(payload)

        # Extract metadata
        metadata = {}
        if "topic" in parsed:
            metadata["topic"] = parsed["topic"]
        if "ttl" in parsed:
            metadata["original_ttl"] = parsed["ttl"]
        if "created_at" in parsed:
            metadata["original_created_at"] = parsed["created_at"]
        # Extract common metadata from payload
        if isinstance(payload, dict):
            for meta_key in ["confidence", "confidence_score", "tool_used", "source_tool", "token_usage", "tokens"]:
                if meta_key in payload:
                    metadata[meta_key] = payload[meta_key]

        now = datetime.now(timezone.utc)

        # Check if artifact already exists
        existing_stmt = select(WorkspaceIntelligence).where(
            WorkspaceIntelligence.space_id == space_id,
            WorkspaceIntelligence.key == key,
        )
        result = await self.db.execute(existing_stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # Archive current version to history
            history_entry = WorkspaceIntelligenceHistory(
                intelligence_id=existing.id,
                space_id=existing.space_id,
                agent_id=existing.agent_id,
                key=existing.key,
                artifact_type=existing.artifact_type,
                payload=existing.payload,
                summary_snippet=existing.summary_snippet,
                artifact_metadata=existing.artifact_metadata,
                version=existing.version,
                created_at=existing.created_at,
                archived_at=now,
            )
            self.db.add(history_entry)

            # Update existing with new data and increment version
            existing.payload = payload
            existing.summary_snippet = summary
            existing.artifact_metadata = metadata if metadata else existing.artifact_metadata
            existing.agent_id = resolved_agent_id
            existing.artifact_type = resolved_type
            existing.version = existing.version + 1
            existing.updated_at = now
            existing.access_count = 0  # Reset access count on update

            await self.db.commit()
            await self.db.refresh(existing)

            logger.info(f"Updated intelligence '{key}' to v{existing.version} for space {space_id}")

            return {
                "status": "updated",
                "id": str(existing.id),
                "key": key,
                "space_id": str(space_id),
                "agent_id": resolved_agent_id,
                "artifact_type": resolved_type.value,
                "version": existing.version,
                "previous_version": existing.version - 1,
                "access_url": f"/api/v1/spaces/{space_id}/intelligence/{key}",
            }
        else:
            # Create new artifact
            new_artifact = WorkspaceIntelligence(
                space_id=space_id,
                agent_id=resolved_agent_id,
                key=key,
                artifact_type=resolved_type,
                payload=payload,
                summary_snippet=summary,
                artifact_metadata=metadata if metadata else None,
                version=1,
                created_at=now,
                updated_at=now,
                access_count=0,
            )
            self.db.add(new_artifact)
            await self.db.commit()
            await self.db.refresh(new_artifact)

            logger.info(f"Created new intelligence '{key}' for space {space_id}")

            return {
                "status": "created",
                "id": str(new_artifact.id),
                "key": key,
                "space_id": str(space_id),
                "agent_id": resolved_agent_id,
                "artifact_type": resolved_type.value,
                "version": 1,
                "access_url": f"/api/v1/spaces/{space_id}/intelligence/{key}",
            }

    async def get_intelligence(
        self,
        space_id: UUID,
        key_or_id: str,
        increment_access: bool = True,
        log_access: bool = False,
    ) -> dict[str, Any] | None:
        """
        Get a specific intelligence artifact by key or ID.

        Supports lookup by either key or ID (UUID).

        Args:
            space_id: The workspace UUID
            key_or_id: The semantic lookup key OR the artifact UUID
            increment_access: Whether to increment access_count
            log_access: Whether to log access in metadata

        Returns:
            The intelligence artifact or None if not found
        """
        # Check if it looks like a UUID (try to parse it)
        try:
            artifact_id = UUID(key_or_id)
            # It's a valid UUID - lookup by ID
            stmt = select(WorkspaceIntelligence).where(
                WorkspaceIntelligence.space_id == space_id,
                WorkspaceIntelligence.id == artifact_id,
            )
        except ValueError:
            # Not a UUID - lookup by key
            stmt = select(WorkspaceIntelligence).where(
                WorkspaceIntelligence.space_id == space_id,
                WorkspaceIntelligence.key == key_or_id,
            )
        result = await self.db.execute(stmt)
        entry = result.scalar_one_or_none()

        if entry is None:
            return None

        # Track access
        if increment_access or log_access:
            if increment_access:
                entry.access_count += 1

            if log_access:
                # Update metadata with access log
                current_metadata = entry.artifact_metadata or {}
                access_log = current_metadata.get("access_log", [])
                access_log.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "access_count": entry.access_count,
                })
                # Keep last 10 access entries
                current_metadata["access_log"] = access_log[-10:]
                current_metadata["last_accessed"] = datetime.now(timezone.utc).isoformat()
                entry.artifact_metadata = current_metadata

            await self.db.commit()
            await self.db.refresh(entry)  # Refresh to avoid lazy-load issues in async

        return {
            "id": str(entry.id),
            "space_id": str(entry.space_id),
            "agent_id": entry.agent_id,
            "key": entry.key,
            "artifact_type": entry.artifact_type.value,
            "payload": entry.payload,
            "summary_snippet": entry.summary_snippet,
            "metadata": entry.artifact_metadata,
            "version": entry.version,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
            "access_count": entry.access_count,
        }

    async def list_intelligence(
        self,
        space_id: UUID,
        limit: int = 50,
        offset: int = 0,
        artifact_type: ArtifactType | str | None = None,
        agent_id: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        key_prefix: str | None = None,
        include_payload: bool = False,
    ) -> dict[str, Any]:
        """
        List intelligence artifacts with deep filtering.

        Args:
            space_id: The workspace UUID (strict multi-tenant isolation)
            limit: Maximum entries to return (max 100)
            offset: Number of entries to skip
            artifact_type: Filter by artifact type (for Sentinel)
            agent_id: Filter by originating agent
            date_from: Filter by created_at >= date_from
            date_to: Filter by created_at <= date_to
            key_prefix: Optional semantic-key prefix filter (for durable collection listings)
            include_payload: Include full payloads for context-compatible list responses

        Returns:
            Paginated list of intelligence artifacts
        """
        limit = min(limit, 100)

        # Build base query with strict space_id isolation
        conditions = [WorkspaceIntelligence.space_id == space_id]

        if artifact_type:
            if isinstance(artifact_type, str):
                try:
                    artifact_type = ArtifactType(artifact_type)
                except ValueError:
                    pass  # Invalid type, skip filter
            if isinstance(artifact_type, ArtifactType):
                conditions.append(WorkspaceIntelligence.artifact_type == artifact_type)

        if agent_id:
            conditions.append(WorkspaceIntelligence.agent_id == agent_id)

        if date_from:
            conditions.append(WorkspaceIntelligence.created_at >= date_from)

        if date_to:
            conditions.append(WorkspaceIntelligence.created_at <= date_to)

        if key_prefix:
            conditions.append(WorkspaceIntelligence.key.startswith(key_prefix, autoescape=True))

        base_query = select(WorkspaceIntelligence).where(and_(*conditions))

        # Get total count
        count_stmt = select(func.count()).select_from(base_query.subquery())
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar() or 0

        # Get paginated results (newest first)
        stmt = base_query.order_by(WorkspaceIntelligence.created_at.desc()).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        entries = result.scalars().all()

        items = []
        for entry in entries:
            item = {
                "id": str(entry.id),
                "agent_id": entry.agent_id,
                "key": entry.key,
                "artifact_type": entry.artifact_type.value,
                "summary_snippet": entry.summary_snippet,
                "version": entry.version,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "access_count": entry.access_count,
            }
            if include_payload:
                item["payload"] = entry.payload
                item["metadata"] = entry.artifact_metadata
            items.append(item)

        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(items) < total,
            "filters_applied": {
                "artifact_type": artifact_type.value if isinstance(artifact_type, ArtifactType) else artifact_type,
                "agent_id": agent_id,
                "date_from": date_from.isoformat() if date_from else None,
                "date_to": date_to.isoformat() if date_to else None,
                "key_prefix": key_prefix,
            },
        }

    async def get_artifact_history(
        self,
        space_id: UUID,
        key: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Get version history for an artifact.

        Returns archived versions ordered by version number (descending).
        """
        stmt = (
            select(WorkspaceIntelligenceHistory)
            .where(
                WorkspaceIntelligenceHistory.space_id == space_id,
                WorkspaceIntelligenceHistory.key == key,
            )
            .order_by(WorkspaceIntelligenceHistory.version.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        entries = result.scalars().all()

        return [
            {
                "id": str(entry.id),
                "intelligence_id": str(entry.intelligence_id),
                "version": entry.version,
                "artifact_type": entry.artifact_type.value,
                "payload": entry.payload,
                "summary_snippet": entry.summary_snippet,
                "metadata": entry.artifact_metadata,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "archived_at": entry.archived_at.isoformat() if entry.archived_at else None,
            }
            for entry in entries
        ]

    async def delete_intelligence(
        self,
        space_id: UUID,
        key_or_id: str,
    ) -> bool:
        """
        Delete an intelligence artifact and its history.

        Supports deletion by either key or ID (UUID).

        Args:
            space_id: The workspace UUID
            key_or_id: The semantic lookup key OR the artifact UUID

        Returns:
            True if deleted, False if not found
        """
        # Check if it looks like a UUID (try to parse it)
        try:
            artifact_id = UUID(key_or_id)
            # It's a valid UUID - delete by ID
            stmt = delete(WorkspaceIntelligence).where(
                WorkspaceIntelligence.space_id == space_id,
                WorkspaceIntelligence.id == artifact_id,
            )
            logger.info(f"Deleting intelligence by ID: {artifact_id}")
        except ValueError:
            # Not a UUID - delete by key
            stmt = delete(WorkspaceIntelligence).where(
                WorkspaceIntelligence.space_id == space_id,
                WorkspaceIntelligence.key == key_or_id,
            )
            logger.info(f"Deleting intelligence by key: {key_or_id}")

        result = await self.db.execute(stmt)
        await self.db.commit()

        deleted = result.rowcount > 0
        if deleted:
            logger.info(f"Deleted intelligence '{key_or_id}' for space {space_id}")

        return deleted

    async def get_with_fallback(
        self,
        space_id: UUID,
        key: str,
    ) -> dict[str, Any] | None:
        """
        Get context with Redis-first, Postgres-fallback strategy.

        This is the "Smart Fallback" for MCP context:get operations:
        1. Check Redis (Hot Cache)
        2. Query workspace_intelligence (The Vault)
        3. Log access in metadata

        Args:
            space_id: The workspace UUID
            key: The semantic lookup key

        Returns:
            Context data from Redis or Postgres, or None if not found
        """
        # Step 1: Check Redis (ephemeral/hot cache)
        redis_key = f"context:{space_id}:{key}"
        raw_value = await self.redis.get(redis_key)

        if raw_value is not None:
            try:
                parsed = json.loads(raw_value)
                return {
                    "source": "redis",
                    "key": key,
                    "value": parsed,
                    "is_ephemeral": True,
                }
            except json.JSONDecodeError:
                return {
                    "source": "redis",
                    "key": key,
                    "value": raw_value,
                    "is_ephemeral": True,
                }

        # Step 2: Fallback to Postgres (The Vault)
        intelligence = await self.get_intelligence(
            space_id=space_id,
            key_or_id=key,
            increment_access=True,
            log_access=False,  # Disable access logging to prevent write amplification
        )

        if intelligence is not None:
            return {
                "source": "postgres",
                "key": key,
                "value": intelligence["payload"],
                "artifact_type": intelligence["artifact_type"],
                "summary_snippet": intelligence["summary_snippet"],
                "metadata": intelligence["metadata"],
                "version": intelligence["version"],
                "is_ephemeral": False,
                "access_count": intelligence["access_count"],
            }

        # Step 3: Not found in either storage
        return None
