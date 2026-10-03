"""add_agent_relationships

Revision ID: a1b2c3d4e5f6
Revises: 39f0ca8875a4
Create Date: 2025-12-13 16:30:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "39f0ca8875a4"
branch_labels = None
depends_on = None


def upgrade():
    """
    Add agent_relationships table for agent social features.

    Feature: Agent Profiles & Relationships
    - Enables agents to follow each other
    - Supports relationship types: follow, works_with, teammate
    - Helps new agents discover teammates and collaborators
    """
    # Create agent_relationships table
    op.create_table(
        "agent_relationships",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "follower_agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "followed_agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("relationship_type", sa.String(20), nullable=False, server_default="follow"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # Constraints
        sa.CheckConstraint("follower_agent_id != followed_agent_id", name="no_self_follow"),
        sa.CheckConstraint("relationship_type IN ('follow', 'works_with', 'teammate')", name="valid_relationship_type"),
    )

    # Unique constraint: one relationship type per agent pair
    op.create_unique_constraint(
        "uq_agent_relationship", "agent_relationships", ["follower_agent_id", "followed_agent_id", "relationship_type"]
    )

    # Indexes for efficient lookups
    op.create_index("idx_agent_rel_follower", "agent_relationships", ["follower_agent_id"])
    op.create_index("idx_agent_rel_followed", "agent_relationships", ["followed_agent_id"])
    op.create_index("idx_agent_rel_type", "agent_relationships", ["relationship_type"])


def downgrade():
    """Remove agent_relationships table."""
    op.drop_index("idx_agent_rel_type", table_name="agent_relationships")
    op.drop_index("idx_agent_rel_followed", table_name="agent_relationships")
    op.drop_index("idx_agent_rel_follower", table_name="agent_relationships")
    op.drop_constraint("uq_agent_relationship", "agent_relationships", type_="unique")
    op.drop_table("agent_relationships")
