"""Fix organization_invites schema from email-based to code-based

Revision ID: 013_fix_organization_invites_schema
Revises: 012_create_organization_tables
Create Date: 2025-08-05 04:50:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '013_fix_organization_invites_schema'
down_revision = '012_create_organization_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Replace email-based invites with code-based invites"""

    # Drop the old email-based table if it exists
    op.execute("DROP TABLE IF EXISTS organization_invites CASCADE")

    # Create new code-based organization_invites table
    op.create_table('organization_invites',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('invite_code', sa.String(12), nullable=False),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('max_uses', sa.Integer(), nullable=True),
        sa.Column('current_uses', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('invite_code', name='organization_invites_invite_code_key')
    )

    # Create indexes for performance
    op.create_index('idx_org_invites_invite_code', 'organization_invites', ['invite_code'])
    op.create_index('idx_org_invites_org_id', 'organization_invites', ['org_id'])
    op.create_index('idx_org_invites_active_expires', 'organization_invites', ['active', 'expires_at'])

    # Add comment documenting the change
    op.execute("""
        COMMENT ON TABLE organization_invites IS
        'Code-based invite system for organizations - replaced email-based system on 2025-08-05'
    """)


def downgrade() -> None:
    """Revert to email-based invites (not recommended)"""

    # Drop the code-based table
    op.drop_index('idx_org_invites_active_expires', 'organization_invites')
    op.drop_index('idx_org_invites_org_id', 'organization_invites')
    op.drop_index('idx_org_invites_invite_code', 'organization_invites')
    op.drop_table('organization_invites')

    # Recreate the old email-based table
    op.create_table('organization_invites',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('invited_by', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('role', sa.String(20), nullable=False, server_default='member'),
        sa.Column('token', sa.String(255), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('accepted_at', sa.DateTime(timezone=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=False), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['invited_by'], ['users.id'], ondelete='CASCADE')
    )
