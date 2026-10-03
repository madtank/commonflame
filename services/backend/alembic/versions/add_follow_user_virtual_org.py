"""Add Follow User virtual organization

Revision ID: add_follow_user_virtual_org
Revises: add_agent_movement_audit_log
Create Date: 2025-08-18 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
import uuid

# revision identifiers, used by Alembic.
revision = 'add_follow_user_virtual_org'
down_revision = '951dc48445c5'
branch_labels = None
depends_on = None

FOLLOW_UUID = '11111111-1111-1111-1111-111111111111'

def upgrade():
    """Add the Follow User virtual organization for agent follow mode."""
    op.execute(f"""
        INSERT INTO organizations (id, name, slug, visibility)
        VALUES ('{FOLLOW_UUID}', 'Follow User (virtual)', 'follow-user', 'system')
        ON CONFLICT (id) DO NOTHING
    """)

def downgrade():
    """Remove the Follow User virtual organization."""
    op.execute(f"""
        DELETE FROM organizations
        WHERE id = '{FOLLOW_UUID}'
    """)
