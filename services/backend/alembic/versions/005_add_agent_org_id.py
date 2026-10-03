"""Add org_id to agents table

Revision ID: 005_add_agent_org_id
Revises: 004_add_current_org_id
Create Date: 2025-07-27 01:35:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '005_add_agent_org_id'
down_revision = '005_enhance_organizations'
branch_labels = None
depends_on = None


def upgrade():
    # Add org_id column to agents table
    op.add_column('agents', sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=True))

    # Update existing agents to inherit org_id from their users
    op.execute("""
        UPDATE agents
        SET org_id = users.org_id
        FROM users
        WHERE agents.user_id = users.id
    """)

    # Make org_id non-nullable after populating
    op.alter_column('agents', 'org_id', nullable=False)

    # Add foreign key constraint
    op.create_foreign_key('fk_agents_org_id', 'agents', 'organizations', ['org_id'], ['id'], ondelete='CASCADE')


def downgrade():
    # Remove foreign key constraint
    op.drop_constraint('fk_agents_org_id', 'agents', type_='foreignkey')

    # Remove org_id column
    op.drop_column('agents', 'org_id')
