"""Add bound_agent_id to credentials table.

Allows credentials to be bound to an agent policy object.
Token inherits agent's space access and permissions at request time.

Revision ID: bac01_bound_agent_cred
Revises: asa01_agent_space_access
Create Date: 2026-03-13

Design: docs/plans/2026-03-13-agent-bound-credentials.md
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "bac01_bound_agent_cred"
down_revision: str = "asa01_agent_space_access"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "credentials",
        sa.Column(
            "bound_agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_credentials_bound_agent_id",
        "credentials",
        ["bound_agent_id"],
    )


def downgrade():
    op.drop_index("ix_credentials_bound_agent_id", table_name="credentials")
    op.drop_column("credentials", "bound_agent_id")
