"""Merge 5 alembic heads into a single head.

Revision ID: merge_heads_2026_04_11
Revises: rls02_agents_space_access, 7155801152f0, ac01, b7c8d9e0f1a2, mrs01_create_message_read_status
Create Date: 2026-04-11

Multiple independent migration branches created parallel heads.
This merge point brings them all back to a single chain so
`alembic upgrade head` works on production deploy.
"""

from alembic import op

revision = "merge_heads_2026_04_11"
down_revision = ("rls02_agents_space_access", "7155801152f0", "ac01", "b7c8d9e0f1a2", "mrs01_create_message_read_status")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
