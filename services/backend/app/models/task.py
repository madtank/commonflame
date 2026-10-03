from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Integer, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSON, JSONB, TSVECTOR
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector
import uuid

from . import Base


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("space_id", "task_number", name="uq_tasks_space_task_number"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    posted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)  # Nullable for agent-created tasks
    posted_by_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"))  # For agent-created tasks
    assigned_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"))
    assignee_type = Column(String(10), nullable=True)  # user | agent
    assignee_id = Column(UUID(as_uuid=True), nullable=True)
    assigned_by_type = Column(String(10), nullable=True)  # user | agent
    assigned_by_id = Column(UUID(as_uuid=True), nullable=True)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    requirements = Column(JSON)
    status = Column(String(20), default="open")  # open, assigned, in_progress, completed, cancelled (LEGACY - will be phased out)
    assignment_status = Column(String(20), default="unassigned")  # Auto-assignment: unassigned, assigned, locked
    work_status = Column(String(20), default="not_started")  # Auto-assignment: not_started, in_progress, blocked, completed, cancelled
    priority = Column(String(10), default="medium")  # low, medium, high, urgent
    task_number = Column(Integer)  # Sequential task number for display
    links = Column(JSON, default=list)  # Artifact links (PRs, docs, deployments)
    task_metadata = Column("metadata", JSONB, nullable=True)  # Flexible metadata (completion notes, etc.)
    deadline = Column(DateTime(timezone=True))
    queue_state = Column(String(20), default="queued")  # queued, active, blocked, snoozed, inactive
    queue_rank = Column(Numeric(20, 10))  # Decimal ranks support insert-between reordering
    reminder_policy = Column(JSONB, nullable=True)
    next_reminder_at = Column(DateTime(timezone=True))
    last_reminded_at = Column(DateTime(timezone=True))
    reminder_count = Column(Integer, nullable=False, default=0)
    snoozed_until = Column(DateTime(timezone=True))
    stale_at = Column(DateTime(timezone=True))
    stale_reason = Column(String(120))
    cancelled_reason = Column(String(120))

    # Task lifecycle controls (Phase A — additive, all nullable). Per the Option A
    # decision (Jacob, 2026-05-30) the lifecycle "focus" axis reuses/extends the
    # existing queue_state value set (gains archived/superseded/deferred/stale/
    # retained) rather than adding a parallel lifecycle_state column. These satellite
    # columns carry the metadata those states require. Reuses existing fields where
    # they already exist: snoozed_until (=defer_until), deadline (=due_at), stale_at/
    # stale_reason, blocked_by_ids, links, the reminder_* subsystem, and task_notes
    # (activity + audit). last_activity_at is backfilled by a SEPARATE post-deploy
    # script, never inline in the migration (avoids the ECS boot-waiter timeout).
    # Design: docs/plans/2026-05-30-task-lifecycle-backend-design.md
    superseded_by_task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    retain_reason = Column(Text, nullable=True)
    review_at = Column(DateTime(timezone=True), nullable=True)
    archived_at = Column(DateTime(timezone=True), nullable=True)
    archived_by = Column(UUID(as_uuid=True), nullable=True)
    archived_reason = Column(String(120), nullable=True)
    lifecycle_reason = Column(String(120), nullable=True)
    lifecycle_changed_at = Column(DateTime(timezone=True), nullable=True)
    lifecycle_changed_by = Column(UUID(as_uuid=True), nullable=True)
    last_activity_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True))
    assigned_at = Column(DateTime(timezone=True))  # When task was assigned to agent
    claimed_at = Column(DateTime(timezone=True))   # When agent claimed/started the task

    # Task dependencies (Phase 2)
    parent_task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    blocked_by_ids = Column(JSONB, default=list)  # Array of task UUIDs this task is blocked by

    # Semantic search (Crystal Prism)
    embedding = Column(Vector(768), nullable=True)  # pgvector embedding for semantic search
    search_vector = Column(TSVECTOR, nullable=True)  # Full-text search vector

    # Relationships
    space = relationship("Space", back_populates="tasks")
    posted_by_user = relationship("User", back_populates="posted_tasks", foreign_keys=[posted_by])
    posted_by_agent = relationship("Agent", foreign_keys=[posted_by_agent_id], back_populates="created_tasks")
    assigned_agent = relationship("Agent", foreign_keys=[assigned_agent_id], back_populates="assigned_tasks")
    notes = relationship("TaskNote", back_populates="task", cascade="all, delete-orphan")
    parent_task = relationship("Task", remote_side=[id], foreign_keys=[parent_task_id], backref="subtasks")

    def __repr__(self):
        return f"<Task(id={self.id}, title='{self.title}', status='{self.status}', priority='{self.priority}')>"
