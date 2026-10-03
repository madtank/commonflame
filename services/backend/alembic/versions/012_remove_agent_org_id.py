"""Remove org_id from agents table - agents belong to users not orgs

Revision ID: 012_remove_agent_org_id
Revises: 011_add_posted_by_agent_id
Create Date: 2025-08-05 15:45:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '012_remove_agent_org_id'
down_revision = '011_add_posted_by_agent_id'
branch_labels = None
depends_on = None


def upgrade():
    # Remove foreign key constraint first
    op.drop_constraint('fk_agents_org_id', 'agents', type_='foreignkey')

    # Remove org_id column from agents table
    # Agents belong to users, not organizations
    op.drop_column('agents', 'org_id')


def downgrade():
    # Re-add org_id column (though this is conceptually wrong)
    op.add_column('agents', sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=True))

    # Update agents to inherit org_id from their users
    op.execute("""
        UPDATE agents
        SET org_id = users.org_id
        FROM users
        WHERE agents.user_id = users.id
    """)

    # Make org_id non-nullable after populating
    op.alter_column('agents', 'org_id', nullable=False)

    # Re-add foreign key constraint
    op.create_foreign_key('fk_agents_org_id', 'agents', 'organizations', ['org_id'], ['id'], ondelete='CASCADE')
