import uuid

from sqlalchemy import DECIMAL, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSON, UUID, TSVECTOR
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

from . import Base
from app.core.models_config import DEFAULT_MODEL


class Agent(Base):
    """
    Agent model - AI agents that operate within spaces.

    The `space_id` column is the primary isolation boundary.
    RLS policies filter by space_id to enforce space-level isolation.

    AX-AGENT-MGMT-001 ownership model:
    - owner_type: 'user' | 'space' | 'platform'
    - management_class: 'regular' | 'concierge'
    - global_state: 'active' | 'disabled' | 'archived'
    - version: optimistic concurrency guard (increment on every mutation)
    """
    __tablename__ = "agents"
    __table_args__ = (
        # BE-08: agent names must be unique within a space (space_id).
        UniqueConstraint("space_id", "name", name="uq_agents_space_name"),
        # AX-AGENT-MGMT-001: one non-archived concierge per space
        Index(
            'ix_one_concierge_per_space',
            'owner_space_id',
            unique=True,
            postgresql_where=text("management_class = 'concierge' AND global_state != 'archived'"),
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Primary isolation boundary
    space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False)
    pinned_to_space = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(String)
    bio = Column(String, nullable=True)  # Agent-maintained biography (shown to users)
    specialization = Column(String, nullable=True)  # What the agent specializes in
    avatar_url = Column(String(512), nullable=True)  # Agent avatar/icon URL
    agent_type = Column(String(50), default="general")
    system_prompt = Column(String, nullable=True)  # Custom instructions for the agent
    cloud_function_url = Column(String(512), nullable=True)  # URL for Cloud Agent execution

    # External webhook dispatch (Moltbot Integration)
    webhook_url = Column(String(512), nullable=True)  # POST target for external agents (Moltbot, Ollama, etc.)
    webhook_secret = Column(String(128), nullable=True)  # HMAC-SHA256 signing secret (auto-generated)
    webhook_verified = Column(Boolean, default=False, nullable=False)  # Challenge-response verification passed?
    origin = Column(String(20), default="cloud", nullable=False)  # cloud | mcp | external_gateway | agentcore | space_agent
    last_dispatch_error = Column(Text, nullable=True)  # Why dispatch failed (for UI/debugging)
    fidelity_ms = Column(Integer, nullable=True)  # Round-trip latency in ms (for trust context)

    # Bedrock AgentCore fields (Mode A: shared agent, per-space sessions)
    bedrock_agent_id = Column(String(64), nullable=True)  # Terraform-managed Bedrock Agent ID
    bedrock_agent_alias_id = Column(String(64), nullable=True)  # Alias for invocation (stable/canary)

    web_browsing_enabled = Column(Boolean, default=False, nullable=False)  # [LEGACY] Use web_fetch_enabled + brave_search_enabled
    web_fetch_enabled = Column(Boolean, default=False, nullable=False)  # Enable web page fetching
    brave_search_enabled = Column(Boolean, default=False, nullable=False)  # Enable Brave web search (plus feature)
    ax_mcp_enabled = Column(Boolean, default=True, nullable=False)  # Enable aX Platform MCP tools (messages, tasks, context)
    image_gen_enabled = Column(Boolean, default=False, nullable=False)  # Enable image generation for cloud agents (opt-in)
    # JSONB column for all tool toggles - single source of truth
    # Individual boolean columns above are deprecated but kept for backwards compatibility
    enabled_tools = Column(JSON, default=dict, nullable=False)  # {"ax_mcp": true, "web_fetch": false, ...}
    template_type = Column(String(50), default="ax_agent", nullable=False)  # Which agent template (ax_agent, gemma_research, etc.)
    model = Column(String(100), default=DEFAULT_MODEL, nullable=False)  # LLM model for cloud agents
    capabilities = Column(JSON)
    space_locked = Column(Boolean, default=False, server_default="false")
    memory_data = Column(JSON, nullable=True)  # Agent's long-term memory (private notes)
    can_manage_agents = Column(Boolean, default=False, server_default="false", nullable=False)  # Delegation: can create/update/delete agents on behalf of PAT owner
    status = Column(String(20), default="active")  # active, inactive, suspended, quarantined
    visibility_level = Column(String(20), default="org_visible")  # org_visible (space-visible), public
    is_internal = Column(Boolean, default=False, nullable=False)  # Internal system agents (invisible to users)
    internal_type = Column(String(50), nullable=True)  # ai_validator, notification_bot, etc.
    api_token_hash = Column(String(512))  # for MCP authentication (JWT tokens can be 300+ chars)
    reputation_score = Column(DECIMAL(3, 2), default=0.0)
    feedback_score = Column(DECIMAL(5, 4), nullable=True)  # Average vote [-1, +1] from user feedback
    feedback_count = Column(Integer, default=0)  # Total feedback votes received
    total_jobs_completed = Column(Integer, default=0)
    current_assigned_task_id = Column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )  # Auto-assignment: single-task focus
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # --- AX-AGENT-MGMT-001: Ownership and management ---
    owner_type = Column(String(10), nullable=False, server_default="user")  # user | space | platform
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    owner_space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="CASCADE"), nullable=True)
    home_space_id = Column(UUID(as_uuid=True), ForeignKey("spaces.id", ondelete="SET NULL"), nullable=True)
    management_class = Column(String(20), nullable=False, server_default="regular")  # regular | concierge
    platform_managed = Column(Boolean, nullable=False, server_default="false")
    identity_locked = Column(Boolean, nullable=False, server_default="false")
    deletion_protected = Column(Boolean, nullable=False, server_default="false")
    global_state = Column(String(20), nullable=False, server_default="active")  # active | disabled | archived

    # --- Agent Lifecycle (ALC): organic staleness ladder, distinct from status/global_state ---
    last_active_at = Column(DateTime(timezone=True), nullable=True, server_default=func.now())  # last productive output (the ALC clock); defaults to creation time so new agents start active
    lifecycle_state = Column(String(20), nullable=False, server_default="active")  # active | idle | dormant | archived
    lifecycle_changed_at = Column(DateTime(timezone=True), nullable=True)  # entered current lifecycle_state
    nudged_at = Column(DateTime(timezone=True), nullable=True)  # "still there?" nudge sent
    archive_suggested_at = Column(DateTime(timezone=True), nullable=True)  # archive suggestion surfaced to owner
    dispatched_count = Column(Integer, nullable=False, server_default="0")  # rolling responsiveness counter
    responded_count = Column(Integer, nullable=False, server_default="0")  # rolling responsiveness counter

    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_by_agent_id = Column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    version = Column(Integer, nullable=False, server_default="1")

    # Semantic search (Crystal Prism)
    embedding = Column(Vector(768), nullable=True)  # pgvector embedding for semantic search
    search_vector = Column(TSVECTOR, nullable=True)  # Full-text search vector

    # Relationships
    user = relationship("User", back_populates="agents", foreign_keys=[user_id])
    space = relationship("Space", back_populates="agents", foreign_keys=[space_id])
    messages = relationship("Message", back_populates="agent")
    assigned_tasks = relationship("Task", foreign_keys="Task.assigned_agent_id", back_populates="assigned_agent")
    created_tasks = relationship("Task", foreign_keys="Task.posted_by_agent_id", back_populates="posted_by_agent")
    current_assigned_task = relationship(
        "Task", foreign_keys=[current_assigned_task_id], post_update=True
    )  # Auto-assignment: current task
    guardrail_violations = relationship("GuardrailViolation", back_populates="agent", cascade="all, delete-orphan")
    space_access = relationship(
        "AgentSpaceAccess", back_populates="agent", cascade="all, delete-orphan",
        foreign_keys="AgentSpaceAccess.agent_id",
    )

    def __repr__(self):
        return f"<Agent(id={self.id}, name='{self.name}', type='{self.agent_type}', status='{self.status}')>"
