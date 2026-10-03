"""
Unified task service for both API and MCP.
Transport-agnostic business logic for task operations.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from uuid import UUID

from sqlalchemy import select, and_, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.actor import Actor
from ..models.task import Task
from ..models.user import User
from ..models.agent import Agent
from ..core.parity import ParityLogger, compute_task_digest
from ..services.notifications_service import NotificationsService

logger = logging.getLogger(__name__)


TASK_STATUS_MAPPING = {
    "open": "not_started",
    "assigned": "in_progress",
    "not_started": "not_started",
    "in_progress": "in_progress",
    "blocked": "blocked",
    "completed": "completed",
    "cancelled": "cancelled",
}
TERMINAL_TASK_STATUSES = {"completed", "cancelled"}


def _task_queue_state_for_status(work_status: Optional[str]) -> str:
    if work_status == "in_progress":
        return "active"
    if work_status == "blocked":
        return "blocked"
    if work_status in TERMINAL_TASK_STATUSES:
        return "inactive"
    return "queued"


def _normalize_task_status(status: str) -> str:
    if status not in TASK_STATUS_MAPPING:
        supported = ", ".join(sorted(TASK_STATUS_MAPPING))
        raise ValueError(f"Unsupported task status {status!r}. Supported values: {supported}")
    return TASK_STATUS_MAPPING[status]


def _apply_task_status_update(task: Task, status: str, now: Optional[datetime] = None) -> None:
    """Apply status lifecycle side effects consistently for service-level updates."""
    now = now or datetime.now(timezone.utc)
    work_status = _normalize_task_status(status)

    # Keep the legacy status column in sync while exposing work_status as canonical.
    task.status = work_status
    task.work_status = work_status
    task.queue_state = _task_queue_state_for_status(work_status)

    if work_status == "completed":
        task.completed_at = task.completed_at or now
    elif getattr(task, "completed_at", None) is not None:
        task.completed_at = None

    if work_status in TERMINAL_TASK_STATUSES:
        task.next_reminder_at = None
        task.snoozed_until = None


# Define capabilities for tasks
CAP_TASKS_CREATE = "tasks:create"
CAP_TASKS_UPDATE = "tasks:update"
CAP_TASKS_ASSIGN = "tasks:assign"
CAP_TASKS_COMPLETE = "tasks:complete"
CAP_TASKS_DELETE = "tasks:delete"
CAP_TASKS_VIEW = "tasks:view"


class TasksService:
    """Transport-agnostic task operations used by both API and MCP."""

    def __init__(self, db: AsyncSession, redis_client=None, sse_broker=None):
        self.db = db
        self.redis = redis_client
        self.sse = sse_broker
        self.parity_logger = ParityLogger("tasks")
        self.notifications = NotificationsService(db, redis_client)

    # --- helpers -------------------------------------------------------------

    async def _validate_assignee(
        self, *, space_id: UUID, assignee_id: Optional[str]
    ) -> Optional[UUID]:
        """Validate assignee exists in organization."""
        if not assignee_id:
            return None

        try:
            aid = uuid.UUID(str(assignee_id))
        except (ValueError, AttributeError):
            raise ValueError("Invalid assignee ID format")

        # Check if assignee is a user or agent in the org
        # This would query both users and agents tables
        # For now, simplified validation
        return aid

    async def _resolve_actor_context(self, actor: Actor) -> Dict[str, Optional[str]]:
        """Derive display information for the actor initiating a task event."""
        context: Dict[str, Optional[str]] = {
            "id": str(actor.id),
            "type": actor.type,
            "display_name": None,
            "owner_user_id": None,
        }

        try:
            if actor.type == "human":
                result = await self.db.execute(
                    select(User.username, User.full_name, User.email).where(User.id == actor.id)
                )
                row = result.first()
                if row:
                    username, full_name, email = row
                    context["display_name"] = username or full_name or email
            elif actor.type == "agent":
                result = await self.db.execute(
                    select(Agent.name, Agent.user_id).where(Agent.id == actor.id)
                )
                row = result.first()
                if row:
                    name, owner_user_id = row
                    context["display_name"] = name
                    context["owner_user_id"] = str(owner_user_id) if owner_user_id else None
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Failed to resolve actor context: %s", exc)

        return context

    async def _broadcast_task_event(
        self,
        *,
        space_id: UUID,
        task: Task,
        event_type: str,
        actor: Actor,
        adapter: str,
    ) -> None:
        """Broadcast task event via SSE and Redis streams."""
        assigned_agent_ref = None
        if task.assigned_agent_id:
            assigned_agent_ref = str(task.assigned_agent_id)

        payload_task: Dict[str, Any] = {
            "id": str(task.id),
            "title": task.title,
            "status": task.status,
            "priority": task.priority,
            "work_status": getattr(task, "work_status", None),
            "due_date": task.deadline.isoformat() if getattr(task, "deadline", None) else None,
            "updated_at": (task.updated_at or datetime.utcnow()).isoformat(),
            "assigned_agent_id": assigned_agent_ref,
        }

        if task.assigned_agent is not None:
            payload_task["assigned_agent"] = {
                "id": str(task.assigned_agent.id),
                "name": task.assigned_agent.name,
                "owner_user_id": str(task.assigned_agent.user_id) if task.assigned_agent.user_id else None,
            }
        else:
            payload_task["assigned_agent"] = None

        actor_context = await self._resolve_actor_context(actor)
        event_payload: Dict[str, Any] = {
            "type": event_type,
            "task": payload_task,
            "actor": actor_context,
            "adapter": adapter,
            "space_id": str(space_id),
            # Backward compatible fields (existing consumers expect flat structure)
            "id": payload_task["id"],
            "title": payload_task["title"],
            "status": payload_task["status"],
            "priority": payload_task["priority"],
            "assigned_to": assigned_agent_ref,
            "updated_at": payload_task["updated_at"],
            "updated_by": actor_context.get("display_name") or actor_context["id"],
        }

        if self.sse:
            try:
                await self.sse.publish(
                    space_id=str(space_id),
                    event="task_update",
                    data=event_payload,
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to publish task update to in-memory SSE: %s", exc)

        try:
            # from mcp_modular.services.event_publishers import publish_task_event


            # await publish_task_event(
            #     task_id=str(task.id),
            #     event_type="created",
            #     space_id=str(task.space_id),
            #     data={"title": task.title, "priority": task.priority},
            # )
            pass # Stub for publish_task_event
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to publish task update to MCP bus: %s", exc)

    async def _get_auto_assignee(
        self, *, space_id: UUID, task_type: str, workload_balance: bool = True
    ) -> Optional[UUID]:
        """
        Intelligence for auto-assignment.
        This is where MCP can get smarter suggestions.
        """
        # TODO: Implement smart assignment logic
        # - Check agent capabilities
        # - Balance workload
        # - Consider task type affinity
        return None

    # --- commands ------------------------------------------------------------

    async def create(
        self,
        *,
        actor: Actor,
        title: str,
        description: Optional[str] = None,
        priority: str = "medium",
        assigned_to: Optional[str] = None,
        due_date: Optional[datetime] = None,
        metadata: Optional[Dict[str, Any]] = None,
        adapter: str = "unknown"  # "api" or "mcp" for parity tracking
    ) -> Task:
        """
        Create a task with unified business logic.
        Used by both API and MCP endpoints.
        """
        # Permission check
        actor.require(CAP_TASKS_CREATE)

        # Validate title
        if not title or not title.strip():
            raise ValueError("Task title cannot be empty")

        if len(title) > 200:
            raise ValueError("Task title exceeds maximum length of 200 characters")

        # Validate assignee if provided
        assignee_id = await self._validate_assignee(
            space_id=actor.space_id,
            assignee_id=assigned_to
        )

        # Auto-assignment if not specified (MCP intelligence)
        if not assignee_id and adapter == "mcp":
            assignee_id = await self._get_auto_assignee(
                space_id=actor.space_id,
                task_type=metadata.get("type", "general") if metadata else "general"
            )

        # Create task
        task = Task(
            id=uuid.uuid4(),
            space_id=actor.space_id,
            title=title,
            description=description,
            status="not_started",
            priority=priority,
            assigned_agent_id=assignee_id,
            created_by=actor.id,
            due_date=due_date,
            task_metadata=metadata or {},
        )

        if assignee_id:
            setattr(task, "assigned_to", assignee_id)

        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task, ["assigned_agent"])

        # Broadcast SSE event
        await self._broadcast_task_event(
            space_id=actor.space_id,
            task=task,
            event_type="created",
            actor=actor,
            adapter=adapter,
        )

        if task.assigned_agent is not None:
            actor_agent_id = actor.id if actor.type == "agent" else None
            try:
                await self.notifications.record_assignment(
                    space_id=actor.space_id,
                    task=task,
                    assignee=task.assigned_agent,
                    actor_agent_id=actor_agent_id,
                )
            except Exception:
                pass

        # Log for parity verification
        digest = compute_task_digest(task)
        self.parity_logger.log_operation(
            adapter=adapter,
            actor_id=actor.id,
            space_id=actor.space_id,
            action="create",
            task_id=task.id,
            digest=digest
        )

        return task

    async def update(
        self,
        *,
        actor: Actor,
        task_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None,
        assigned_to: Optional[str] = None,
        due_date: Optional[datetime] = None,
        adapter: str = "unknown"
    ) -> Task:
        """Update a task."""
        actor.require(CAP_TASKS_UPDATE)

        # Get task
        try:
            tid = uuid.UUID(str(task_id))
        except (ValueError, AttributeError):
            raise ValueError("Invalid task ID format")

        result = await self.db.execute(
            select(Task).where(
                and_(
                    Task.id == tid,
                    Task.space_id == actor.space_id
                )
            )
        )
        task = result.scalar_one_or_none()
        if not task:
            raise ValueError("Task not found or not in same organization")

        # Validate status before mutating any non-status fields so a mixed update
        # cannot partially dirty the session when the status value is unsupported.
        if status is not None:
            _normalize_task_status(status)

        # Update fields
        if title is not None:
            task.title = title
        if description is not None:
            task.description = description
        if priority is not None:
            task.priority = priority
        if status is not None:
            _apply_task_status_update(task, status)
        previous_assignee = task.assigned_agent_id
        if assigned_to is not None:
            new_assignee = await self._validate_assignee(
                space_id=actor.space_id,
                assignee_id=assigned_to
            )
            task.assigned_agent_id = new_assignee
            setattr(task, "assigned_to", new_assignee)
        if due_date is not None:
            task.due_date = due_date

        task.updated_at = datetime.now(timezone.utc)

        await self.db.commit()
        await self.db.refresh(task, ["assigned_agent"])

        if previous_assignee != task.assigned_agent_id and task.assigned_agent is not None:
            actor_agent_id = actor.id if actor.type == "agent" else None
            try:
                await self.notifications.record_assignment(
                    space_id=actor.space_id,
                    task=task,
                    assignee=task.assigned_agent,
                    actor_agent_id=actor_agent_id,
                )
            except Exception:
                pass

        # Broadcast SSE event
        await self._broadcast_task_event(
            space_id=actor.space_id,
            task=task,
            event_type="updated",
            actor=actor,
            adapter=adapter,
        )

        # Log for parity
        digest = compute_task_digest(task)
        self.parity_logger.log_operation(
            adapter=adapter,
            actor_id=actor.id,
            space_id=actor.space_id,
            action="update",
            task_id=task.id,
            digest=digest
        )

        return task

    async def assign(
        self,
        *,
        actor: Actor,
        task_id: str,
        assignee_id: str,
        adapter: str = "unknown"
    ) -> Task:
        """Assign a task to a user or agent."""
        actor.require(CAP_TASKS_ASSIGN)

        # Use update internally
        return await self.update(
            actor=actor,
            task_id=task_id,
            assigned_to=assignee_id,
            adapter=adapter
        )

    async def complete(
        self,
        *,
        actor: Actor,
        task_id: str,
        completion_notes: Optional[str] = None,
        adapter: str = "unknown"
    ) -> Task:
        """Mark a task as completed."""
        actor.require(CAP_TASKS_COMPLETE)

        # Get and update task
        task = await self.update(
            actor=actor,
            task_id=task_id,
            status="completed",
            adapter=adapter
        )

        # Add completion metadata
        if completion_notes:
            if not task.task_metadata:
                task.task_metadata = {}
            task.task_metadata["completion_notes"] = completion_notes
            task.task_metadata["completed_by"] = str(actor.id)
            task.task_metadata["completed_at"] = datetime.now(timezone.utc).isoformat()

            await self.db.commit()
            await self.db.refresh(task)

        return task

    async def list_tasks(
        self,
        *,
        actor: Actor,
        status: Optional[str] = None,
        assigned_to: Optional[str] = None,
        priority: Optional[str] = None,
        limit: int = 50,
        include_completed: bool = False,
        adapter: str = "unknown"
    ) -> List[Task]:
        """
        List tasks with unified query logic.
        Both API and MCP use this, format results differently.
        """
        actor.require(CAP_TASKS_VIEW)

        # Build query
        query = select(Task).where(Task.space_id == actor.space_id)

        if status:
            query = query.where(Task.status == status)
        elif not include_completed:
            query = query.where(Task.status != "completed")

        if assigned_to:
            try:
                aid = uuid.UUID(str(assigned_to))
                query = query.where(Task.assigned_agent_id == aid)
            except (ValueError, AttributeError):
                pass  # Invalid ID, return empty

        if priority:
            query = query.where(Task.priority == priority)

        query = query.order_by(Task.created_at.desc()).limit(limit)

        result = await self.db.execute(query)
        tasks = result.scalars().all()

        # Log for parity (list operations)
        self.parity_logger.log_operation(
            adapter=adapter,
            actor_id=actor.id,
            space_id=actor.space_id,
            action="list",
            task_count=len(tasks)
        )

        return list(tasks)

    async def get_task_intelligence(
        self,
        *,
        actor: Actor,
        space_id: UUID
    ) -> Dict[str, Any]:
        """
        Intelligence augmentation for MCP.
        Provides workload analysis, bottlenecks, suggestions.
        UI doesn't need this, but MCP can use for context.
        """
        # TODO: Implement intelligence gathering
        # - Task distribution by assignee
        # - Overdue tasks
        # - Bottlenecks
        # - Suggested priorities
        return {
            "total_tasks": 0,
            "overdue": 0,
            "unassigned": 0,
            "bottlenecks": [],
            "suggestions": []
        }
