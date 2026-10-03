"""Add router columns: default_route_to on organization_memberships, space_locked on agents

Revision ID: router01
Revises: msg_perf_01
Create Date: 2026-02-17
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = 'router01'
down_revision: Union[str, None] = 'msg_perf_01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Per-user-per-space default routing preference
    op.add_column('organization_memberships', sa.Column(
        'default_route_to',
        sa.dialects.postgresql.UUID(as_uuid=True),
        sa.ForeignKey('agents.id', ondelete='SET NULL'),
        nullable=True,
        comment='Default agent to route messages to in this space (bypasses router classification)'
    ))

    # Prevent agents from being moved out of private/sensitive spaces
    op.add_column('agents', sa.Column(
        'space_locked',
        sa.Boolean(),
        server_default=sa.text('false'),
        nullable=False,
        comment='When true, agent cannot be moved to another space without owner confirmation'
    ))


def downgrade() -> None:
    op.drop_column('agents', 'space_locked')
    op.drop_column('organization_memberships', 'default_route_to')
