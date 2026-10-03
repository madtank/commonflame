"""Temporarily disable RLS until endpoint migration is complete

Revision ID: z9y8x7w6v5u4
Revises: q1r2s3t4u5v6
Create Date: 2026-01-08

CONTEXT: Production outage on 2026-01-07 caused by RLS being enabled before
endpoints were updated to set RLS context. This migration explicitly disables
RLS until the "Incremental Shield Protocol" migration is complete.

This migration will be reverted (by a future migration) once all endpoints
are migrated to use SecureSession and tested.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'z9y8x7w6v5u4'
down_revision: Union[str, None] = 'q1r2s3t4u5v6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Disable RLS on all tables until endpoint migration is complete."""
    # Disable RLS (keeps policies but doesn't enforce them)
    op.execute("ALTER TABLE tasks DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agents DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations DISABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Re-enable RLS when ready (or via future migration)."""
    op.execute("ALTER TABLE tasks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tasks FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE messages FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agents FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
