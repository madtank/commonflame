"""Add can_manage_agents column to agents table.

Revision ID: am01_can_manage_agents
Revises: sp01_rename_tables
Create Date: 2026-03-13

Boolean flag enabling agent-to-agent delegation: agents with
can_manage_agents=True can create/update/delete other agents
on behalf of the PAT owner.
"""
from alembic import op
import sqlalchemy as sa

revision: str = "am01_can_manage_agents"
down_revision: str = "sp01_rename_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "can_manage_agents",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "can_manage_agents")
