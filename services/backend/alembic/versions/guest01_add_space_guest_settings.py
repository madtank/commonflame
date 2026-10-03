"""Add show_member_list_to_guests to organizations

Revision ID: guest01
Revises: channels01
Create Date: 2026-02-21

Owned by: @logic_runner_677
Story: docs/guest-space-access-spec.md §6 (guest visibility settings)

Adds the show_member_list_to_guests boolean to organizations.
When true, guests can see who else is in the space (member list).
When false (default), guests only see their own membership.

Spec: §6.3 — "Space admins can choose whether guests see the member list"
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = 'guest01'
down_revision: Union[str, None] = 'channels01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'organizations',
        sa.Column(
            'show_member_list_to_guests',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
            comment='When true, guests can see the space member list. Default: false (deny).',
        ),
    )


def downgrade() -> None:
    op.drop_column('organizations', 'show_member_list_to_guests')
