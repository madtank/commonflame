"""add_pending_relationship_requests

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2025-12-15 22:30:00.000000

Adds pending_relationship_requests table for invite/accept workflow.
teammate and works_with relationships now require acceptance before becoming active.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    """
    Add pending_relationship_requests table for invite/accept workflow.

    Security feature: teammate/works_with relationships require acceptance
    before becoming active, especially important for cross-user agents.
    """
    op.create_table(
        "pending_relationship_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "requester_agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "target_agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("relationship_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("message", sa.Text, nullable=True),  # Optional message from requester
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW() + INTERVAL '30 days'"),
            nullable=True,
        ),
        # Constraints
        sa.CheckConstraint("requester_agent_id != target_agent_id", name="no_self_request"),
        sa.CheckConstraint("relationship_type IN ('works_with', 'teammate')", name="valid_request_relationship_type"),
        sa.CheckConstraint("status IN ('pending', 'accepted', 'rejected', 'expired')", name="valid_request_status"),
    )

    # Unique constraint: one pending request per relationship type between agents
    op.create_unique_constraint(
        "uq_pending_request",
        "pending_relationship_requests",
        ["requester_agent_id", "target_agent_id", "relationship_type"],
    )

    # Index for efficient lookup of pending requests targeting an agent
    op.create_index(
        "idx_pending_requests_target_status",
        "pending_relationship_requests",
        ["target_agent_id", "status"],
    )

    # Index for looking up requests by requester
    op.create_index(
        "idx_pending_requests_requester",
        "pending_relationship_requests",
        ["requester_agent_id", "status"],
    )


def downgrade():
    """Remove pending_relationship_requests table."""
    op.drop_index("idx_pending_requests_requester", table_name="pending_relationship_requests")
    op.drop_index("idx_pending_requests_target_status", table_name="pending_relationship_requests")
    op.drop_constraint("uq_pending_request", "pending_relationship_requests", type_="unique")
    op.drop_table("pending_relationship_requests")
