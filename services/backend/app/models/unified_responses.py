"""
Unified Response Models for API-MCP Parity
Ensures identical data structures between web API and MCP tools
"""
from typing import Optional, Dict, List, Any, Union
from datetime import datetime
from pydantic import BaseModel
from dataclasses import dataclass


@dataclass
class UnifiedCreatorInfo:
    """Unified creator information for both users and agents"""
    id: str
    username: str
    full_name: str
    type: str  # "user" or "agent"


@dataclass
class UnifiedAgentInfo:
    """Unified agent information"""
    id: str
    name: str
    agent_type: str


@dataclass
class UnifiedTaskData:
    """Core unified task data structure used by both API and MCP"""
    # Core identification
    id: str
    task_number: Optional[int]
    task_display_id: str

    # Task content
    title: str
    description: Optional[str]
    requirements: Optional[Dict[str, Any]]

    # Status and priority
    work_status: str  # The actual status (not_started, in_progress, completed, etc.)
    assignment_status: str  # Assignment state (unassigned, assigned, locked)
    priority: str

    # Timestamps
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]
    deadline: Optional[datetime]

    # Relationships
    posted_by: Optional[UnifiedCreatorInfo]
    assigned_agent: Optional[UnifiedAgentInfo]

    # Agent-specific context (MCP-only fields)
    is_mine: bool = False
    can_assign: bool = False


class UnifiedTaskResponse:
    """Unified task response that can be formatted for different clients"""

    def __init__(self, task_data: UnifiedTaskData):
        self.data = task_data

    def for_web_api(self) -> Dict[str, Any]:
        """Format for web API consumption (matches TaskResponse)"""
        return {
            "id": self.data.id,
            "task_display_id": self.data.task_display_id,
            "title": self.data.title,
            "description": self.data.description,
            "requirements": self.data.requirements or {},
            "status": self.data.work_status,  # Web API expects 'status' field
            "priority": self.data.priority,
            "deadline": self.data.deadline,
            "created_at": self.data.created_at,
            "updated_at": self.data.updated_at,
            "completed_at": self.data.completed_at,
            "posted_by": {
                "id": self.data.posted_by.id,
                "username": self.data.posted_by.username,
                "full_name": self.data.posted_by.full_name,
                "type": self.data.posted_by.type
            } if self.data.posted_by else None,
            "assigned_agent": {
                "id": self.data.assigned_agent.id,
                "name": self.data.assigned_agent.name,
                "agent_type": self.data.assigned_agent.agent_type
            } if self.data.assigned_agent else None
        }

    def for_mcp_tools(self) -> Dict[str, Any]:
        """Format for MCP tool consumption (includes agent-specific context)"""
        return {
            "id": self.data.id,
            "task_number": self.data.task_number,
            "task_display_id": self.data.task_display_id,
            "title": self.data.title,
            "description": self.data.description,
            "requirements": self.data.requirements or {},

            # Both status fields for MCP (more granular control)
            "work_status": self.data.work_status,
            "assignment_status": self.data.assignment_status,
            "priority": self.data.priority,

            # Timestamps as ISO strings for JSON serialization
            "created_at": self.data.created_at.isoformat() if self.data.created_at else None,
            "updated_at": self.data.updated_at.isoformat() if self.data.updated_at else None,
            "completed_at": self.data.completed_at.isoformat() if self.data.completed_at else None,
            "deadline": self.data.deadline.isoformat() if self.data.deadline else None,

            # Creator info (simplified for agent consumption)
            "creator": self.data.posted_by.full_name if self.data.posted_by else "Unknown",
            "creator_type": self.data.posted_by.type if self.data.posted_by else None,

            # Agent assignment (simplified)
            "assigned_to": self.data.assigned_agent.name if self.data.assigned_agent else None,
            "assigned_agent_id": self.data.assigned_agent.id if self.data.assigned_agent else None,

            # Agent-specific context
            "is_mine": self.data.is_mine,
            "can_assign": self.data.can_assign
        }

    def for_mcp_headers_only(self) -> Dict[str, Any]:
        """Format for MCP header-only listing (minimal data for overview)"""
        return {
            "id": self.data.id,
            "task_number": self.data.task_number,
            "task_display_id": self.data.task_display_id,
            "title": self.data.title,
            "priority": self.data.priority,
            "assignment_status": self.data.assignment_status,
            "work_status": self.data.work_status,
            "assigned_to": self.data.assigned_agent.name if self.data.assigned_agent else None,
            "creator": self.data.posted_by.full_name if self.data.posted_by else "Unknown",
            "created_at": self.data.created_at.isoformat() if self.data.created_at else "",
            "updated_at": self.data.updated_at.isoformat() if self.data.updated_at else "",
            "is_mine": self.data.is_mine,
            "can_assign": self.data.can_assign
        }


class UnifiedTaskStatsData:
    """Unified task statistics for dashboard views"""

    def __init__(self, stats: Dict[str, int]):
        self.total = stats.get('total', 0)
        self.open = stats.get('open', 0)
        self.available = stats.get('available', 0)
        self.assigned = stats.get('assigned', 0)
        self.completed = stats.get('completed', 0)
        self.in_progress = stats.get('in_progress', 0)
        self.cancelled = stats.get('cancelled', 0)

    def to_dict(self) -> Dict[str, int]:
        """Convert to dictionary"""
        return {
            'total': self.total,
            'open': self.open,
            'available': self.available,
            'assigned': self.assigned,
            'completed': self.completed,
            'in_progress': self.in_progress,
            'cancelled': self.cancelled
        }

    def summary_text(self) -> str:
        """Generate summary text matching web interface format"""
        return f"{self.total} total, {self.open} open, {self.available} available, {self.assigned} assigned, {self.completed} completed, {self.in_progress} in progress"

    def for_web_api(self) -> Dict[str, Any]:
        """Format for web API dashboard"""
        return {
            "stats": self.to_dict(),
            "summary": self.summary_text(),
            "breakdown": {
                "active": self.open,
                "pending": self.available,
                "in_progress": self.in_progress,
                "completed": self.completed
            }
        }

    def for_mcp_dashboard(self) -> Dict[str, Any]:
        """Format for MCP dashboard tool"""
        return {
            "dashboard_type": "task_summary",
            "stats": self.to_dict(),
            "summary_text": self.summary_text(),
            "breakdown": {
                "actionable": self.available,  # Tasks agents can take
                "assigned": self.assigned,     # Tasks being worked on
                "completed": self.completed,   # Done tasks
                "total_active": self.open      # Everything not done
            }
        }


@dataclass
class UnifiedAgentWorkload:
    """Unified agent workload information"""
    agent_name: str
    total_tasks: int
    active_tasks: int
    completed_tasks: int = 0


class UnifiedTaskListResponse:
    """Unified task list response for both API and MCP"""

    def __init__(
        self,
        tasks: List[UnifiedTaskData],
        total: int,
        limit: int,
        offset: int = 0,
        filters: Optional[Dict[str, Any]] = None,
        agent_name: Optional[str] = None
    ):
        self.tasks = [UnifiedTaskResponse(task) for task in tasks]
        self.total = total
        self.limit = limit
        self.offset = offset
        self.filters = filters or {}
        self.agent_name = agent_name

    def for_web_api(self) -> Dict[str, Any]:
        """Format for web API response"""
        return {
            "tasks": [task.for_web_api() for task in self.tasks],
            "total": self.total,
            "limit": self.limit,
            "offset": self.offset,
            "filters": self.filters
        }

    def for_mcp_tools(self) -> Dict[str, Any]:
        """Format for MCP tool response"""
        return {
            "list_type": "task_headers",
            "agent_name": self.agent_name,
            "tasks": [task.for_mcp_headers_only() for task in self.tasks],
            "count": len(self.tasks),
            "total": self.total,
            "limit": self.limit,
            "filters": self.filters,
            "generated_at": datetime.now().isoformat()
        }


class UnifiedResponseFactory:
    """Factory for creating unified responses from database objects"""

    @staticmethod
    def from_db_task(
        task_db_obj,
        agent_context: Optional[Dict[str, Any]] = None
    ) -> UnifiedTaskData:
        """Create unified task data from database task object"""

        # Extract creator info
        posted_by = None
        if hasattr(task_db_obj, 'posted_by_agent') and task_db_obj.posted_by_agent:
            posted_by = UnifiedCreatorInfo(
                id=str(task_db_obj.posted_by_agent.id),
                username=f"@{task_db_obj.posted_by_agent.name}",
                full_name=f"Agent: {task_db_obj.posted_by_agent.name}",
                type="agent"
            )
        elif hasattr(task_db_obj, 'posted_by_user') and task_db_obj.posted_by_user:
            posted_by = UnifiedCreatorInfo(
                id=str(task_db_obj.posted_by_user.id),
                username=task_db_obj.posted_by_user.username,
                full_name=task_db_obj.posted_by_user.full_name,
                type="user"
            )

        # Extract assigned agent info
        assigned_agent = None
        if hasattr(task_db_obj, 'assigned_agent') and task_db_obj.assigned_agent:
            assigned_agent = UnifiedAgentInfo(
                id=str(task_db_obj.assigned_agent.id),
                name=task_db_obj.assigned_agent.name,
                agent_type=task_db_obj.assigned_agent.agent_type or "unknown"
            )

        # Generate display ID
        display_id = f"task_{task_db_obj.task_number:06d}" if task_db_obj.task_number else "task_legacy"

        # Agent context (for MCP tools)
        is_mine = False
        can_assign = False
        if agent_context:
            is_mine = (
                assigned_agent and
                agent_context.get('agent_id') == assigned_agent.id
            )
            can_assign = agent_context.get('can_assign', False)

        return UnifiedTaskData(
            id=str(task_db_obj.id),
            task_number=task_db_obj.task_number,
            task_display_id=display_id,
            title=task_db_obj.title or "",
            description=task_db_obj.description,
            requirements=task_db_obj.requirements,
            work_status=task_db_obj.work_status or "not_started",
            assignment_status=task_db_obj.assignment_status or "unassigned",
            priority=task_db_obj.priority or "medium",
            created_at=task_db_obj.created_at,
            updated_at=task_db_obj.updated_at,
            completed_at=task_db_obj.completed_at,
            deadline=task_db_obj.deadline,
            posted_by=posted_by,
            assigned_agent=assigned_agent,
            is_mine=is_mine,
            can_assign=can_assign
        )
