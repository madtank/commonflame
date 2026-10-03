"""Add users.last_login_at for activity tracking.

Revision ID: llr01_last_login
Revises: ua02_user_audit_events
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "llr01_last_login"
down_revision: Union[str, None] = "ua02_user_audit_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "last_login_at")
