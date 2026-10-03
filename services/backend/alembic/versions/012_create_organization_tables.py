"""Create organization membership and invite tables

Revision ID: 012_create_organization_tables
Revises: 011_add_posted_by_agent_id
Create Date: 2025-08-05 03:40:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '012_create_organization_tables'
down_revision = '011_add_posted_by_agent_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create organization membership and invite tables"""

    # Create organization_membership table
    op.create_table('organization_membership',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(50), nullable=False, server_default='member'),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('user_id', 'org_id', name='uq_user_org_membership')
    )

    # Create indexes for organization_membership
    op.create_index('idx_org_membership_user_id', 'organization_membership', ['user_id'])
    op.create_index('idx_org_membership_org_id', 'organization_membership', ['org_id'])
    op.create_index('idx_org_membership_user_org', 'organization_membership', ['user_id', 'org_id'])

    # Create organization_invites table
    op.create_table('organization_invites',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('invite_code', sa.String(12), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('current_uses', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('invite_code', name='uq_invite_code')
    )

    # Create indexes for organization_invites
    op.create_index('idx_org_invites_invite_code', 'organization_invites', ['invite_code'])
    op.create_index('idx_org_invites_org_id', 'organization_invites', ['org_id'])
    op.create_index('idx_org_invites_active_expires', 'organization_invites', ['active', 'expires_at'])

    # Migrate existing user org_id to organization_membership
    # This ensures existing users are members of their default organization
    op.execute("""
        INSERT INTO organization_membership (id, user_id, org_id, role, joined_at)
        SELECT gen_random_uuid(), id, org_id,
               CASE WHEN role = 'admin' THEN 'admin' ELSE 'member' END,
               created_at
        FROM users
        WHERE org_id IS NOT NULL
        ON CONFLICT (user_id, org_id) DO NOTHING
    """)

    # Add current_org_id users to membership if not already there
    op.execute("""
        INSERT INTO organization_membership (id, user_id, org_id, role, joined_at)
        SELECT gen_random_uuid(), id, current_org_id, 'member', NOW()
        FROM users
        WHERE current_org_id IS NOT NULL
        AND current_org_id != org_id
        AND NOT EXISTS (
            SELECT 1 FROM organization_membership
            WHERE user_id = users.id AND org_id = users.current_org_id
        )
    """)


def downgrade() -> None:
    """Drop organization membership and invite tables"""

    # Drop indexes
    op.drop_index('idx_org_invites_active_expires', 'organization_invites')
    op.drop_index('idx_org_invites_org_id', 'organization_invites')
    op.drop_index('idx_org_invites_invite_code', 'organization_invites')
    op.drop_index('idx_org_membership_user_org', 'organization_membership')
    op.drop_index('idx_org_membership_org_id', 'organization_membership')
    op.drop_index('idx_org_membership_user_id', 'organization_membership')

    # Drop tables
    op.drop_table('organization_invites')
    op.drop_table('organization_membership')
