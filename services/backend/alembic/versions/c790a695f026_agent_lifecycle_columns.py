"""agent_lifecycle_columns

Revision ID: c790a695f026
Revises: 607c0ca18ecf
Create Date: 2026-05-30 00:34:10.401539

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c790a695f026'
down_revision: Union[str, None] = '607c0ca18ecf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALC lifecycle fields (see docs/plans/2026-05-29-agent-lifecycle-design.md).
    # lifecycle_state is the organic staleness state, distinct from status (operational)
    # and global_state (admin kill-switch). Lifecycle-archive sets global_state separately.
    # server_default now() so agents created after this migration start their clock at
    # creation time (otherwise last_active_at is NULL and the sweep would compute now-None).
    op.add_column(
        "agents",
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")),
    )
    op.add_column(
        "agents",
        sa.Column("lifecycle_state", sa.String(length=20), nullable=False, server_default="active"),
    )
    op.add_column(
        "agents", sa.Column("lifecycle_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("agents", sa.Column("nudged_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "agents", sa.Column("archive_suggested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "agents",
        sa.Column("dispatched_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "agents",
        sa.Column("responded_count", sa.Integer(), nullable=False, server_default="0"),
    )

    # Sweep reads candidates by (lifecycle_state, last_active_at).
    op.create_index(
        "ix_agents_lifecycle_state_last_active",
        "agents",
        ["lifecycle_state", "last_active_at"],
    )

    # Backfill last_active_at from each agent's own message history (an agent's
    # messages are replies = productive output), falling back to updated_at then
    # created_at so nothing seeds null and false-archives on the first sweep.
    op.execute(
        """
        UPDATE agents AS a
        SET last_active_at = COALESCE(
            (SELECT MAX(m.created_at) FROM messages m WHERE m.agent_id = a.id),
            a.updated_at,
            a.created_at
        ),
        lifecycle_changed_at = now()
        """
    )


def downgrade() -> None:
    op.drop_index("ix_agents_lifecycle_state_last_active", table_name="agents")
    for col in (
        "responded_count",
        "dispatched_count",
        "archive_suggested_at",
        "nudged_at",
        "lifecycle_changed_at",
        "lifecycle_state",
        "last_active_at",
    ):
        op.drop_column("agents", col)
