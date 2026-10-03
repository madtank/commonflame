"""
Auto-Assignment Service
Handles automatic task assignment based on agent interactions
Implements single-task constraint and intent-based assignment
"""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from typing import Optional, Dict, Any
from datetime import datetime
import uuid

from ..models.task import Task
from ..models.agent import Agent


class TaskConflictException(HTTPException):
    """Raised when agent tries to assign a new task while already having one assigned"""

    def __init__(self, current_task: Dict[str, Any], requested_task_id: str):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "TASK_ALREADY_ASSIGNED",
                "message": f"You must complete {current_task['task_display_id']} before starting a new task",
                "current_task": current_task,
                "requested_task_id": requested_task_id,
                "completion_options": ["complete", "abandon", "request_help"]
            }
        )


class AutoAssignmentService:
    """Service for handling automatic task assignments"""

    @staticmethod
    async def can_assign_task(agent_id: uuid.UUID, task_id: uuid.UUID, db: AsyncSession) -> bool:
        """
        Check if a task can be assigned to an agent

        Args:
            agent_id: The agent to assign to
            task_id: The task to assign
            db: Database session

        Returns:
            True if assignment is allowed, False otherwise
        """
        # Get agent with current assignment
        agent_result = await db.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()

        if not agent:
            return False

        # Get task details
        # Note: We don't check Task.space_id == agent.space_id because agents belong to users, not orgs
        # The task should be accessible if it's in the user's current org context
        task_result = await db.execute(
            select(Task).where(Task.id == task_id)
        )
        task = task_result.scalar_one_or_none()

        if not task:
            return False

        # Cannot assign completed or cancelled tasks
        if task.work_status in ['completed', 'cancelled']:
            return False

        # Cannot assign if task is already assigned to someone else
        if task.assignment_status == 'assigned' and task.assigned_agent_id != agent_id:
            return False

        # Cannot assign if agent already has a different task assigned
        if agent.current_assigned_task_id and str(agent.current_assigned_task_id) != str(task_id):
            return False

        return True

    @staticmethod
    async def get_agent_current_task(agent_id: uuid.UUID, db: AsyncSession) -> Optional[Dict[str, Any]]:
        """
        Get the current task assigned to an agent

        Args:
            agent_id: The agent ID
            db: Database session

        Returns:
            Task dict if assigned, None otherwise
        """
        # Get agent with current task
        agent_result = await db.execute(
            select(Agent).options(
                selectinload(Agent.current_assigned_task)
            ).where(Agent.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()

        if not agent or not agent.current_assigned_task:
            return None

        task = agent.current_assigned_task
        display_id = f"task_{task.task_number:06d}" if task.task_number else "task_legacy"

        return {
            "id": str(task.id),
            "task_display_id": display_id,
            "title": task.title,
            "description": task.description,
            "assignment_status": task.assignment_status,
            "work_status": task.work_status,
            "priority": task.priority,
            "created_at": task.created_at.isoformat() if task.created_at else None
        }

    @staticmethod
    async def auto_assign_task(
        task_id: uuid.UUID,
        agent_id: uuid.UUID,
        db: AsyncSession,
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Automatically assign a task to an agent

        Args:
            task_id: The task to assign
            agent_id: The agent to assign to
            db: Database session
            force: Whether to force assignment (for emergency override)

        Returns:
            Assignment result dict

        Raises:
            TaskConflictException: If agent has another task assigned
            HTTPException: For other assignment errors
        """
        # Check if agent has current assignment
        if not force:
            current_task = await AutoAssignmentService.get_agent_current_task(agent_id, db)
            if current_task and str(current_task["id"]) != str(task_id):
                raise TaskConflictException(current_task, str(task_id))

        # Verify assignment is allowed
        if not await AutoAssignmentService.can_assign_task(agent_id, task_id, db):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task cannot be assigned to this agent"
            )

        # Get task and agent
        task_result = await db.execute(
            select(Task).where(Task.id == task_id)
        )
        task = task_result.scalar_one_or_none()

        agent_result = await db.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        agent = agent_result.scalar_one_or_none()

        if not task or not agent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Task or agent not found"
            )

        # Perform assignment
        await db.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(
                assignment_status="assigned",
                assigned_agent_id=agent_id,
                work_status="in_progress",
                queue_state="active",
                assigned_at=datetime.utcnow(),
                claimed_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
        )

        await db.execute(
            update(Agent)
            .where(Agent.id == agent_id)
            .values(
                current_assigned_task_id=task_id,
                updated_at=datetime.utcnow()
            )
        )

        await db.commit()

        # Refresh objects
        await db.refresh(task)
        await db.refresh(agent)

        display_id = f"task_{task.task_number:06d}" if task.task_number else "task_legacy"

        return {
            "success": True,
            "task_id": str(task_id),
            "task_display_id": display_id,
            "agent_id": str(agent_id),
            "message": f"Auto-assigned {display_id} to {agent.name}",
            "assignment_status": task.assignment_status,
            "work_status": task.work_status
        }

    @staticmethod
    async def release_assignment(
        agent_id: uuid.UUID,
        db: AsyncSession,
        new_task_status: str = "completed"
    ) -> Dict[str, Any]:
        """
        Release an agent's current task assignment

        Args:
            agent_id: The agent to release
            db: Database session
            new_task_status: Status to set on the released task

        Returns:
            Release result dict
        """
        # Get agent with current task
        current_task = await AutoAssignmentService.get_agent_current_task(agent_id, db)
        if not current_task:
            return {
                "success": True,
                "message": "No task currently assigned"
            }

        task_id = uuid.UUID(current_task["id"])

        # Update task status
        task_update_values = {
            "assignment_status": "unassigned",
            "work_status": new_task_status,
            "queue_state": "inactive" if new_task_status in ["completed", "cancelled"] else "queued",
            "next_reminder_at": None,
            "snoozed_until": None,
            "updated_at": datetime.utcnow()
        }

        if new_task_status in ["completed", "cancelled"]:
            if new_task_status == "completed":
                task_update_values["completed_at"] = datetime.utcnow()
        else:
            task_update_values["completed_at"] = None

        await db.execute(
            update(Task)
            .where(Task.id == task_id)
            .values(**task_update_values)
        )

        # Clear agent assignment
        await db.execute(
            update(Agent)
            .where(Agent.id == agent_id)
            .values(
                current_assigned_task_id=None,
                updated_at=datetime.utcnow()
            )
        )

        await db.commit()

        return {
            "success": True,
            "task_id": str(task_id),
            "task_display_id": current_task["task_display_id"],
            "message": f"Released assignment: {current_task['task_display_id']}",
            "new_status": new_task_status
        }
