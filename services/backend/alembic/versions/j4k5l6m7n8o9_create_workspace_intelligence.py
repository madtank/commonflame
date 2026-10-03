"""create_workspace_intelligence

Revision ID: j4k5l6m7n8o9
Revises: h2i3j4k5l6m7
Create Date: 2026-01-01 14:00:00.000000

Creates the Workspace Intelligence Vault tables for tiered persistence.
This allows ephemeral Redis context to be promoted to permanent Postgres storage.

Features:
- workspace_intelligence: Main table for promoted artifacts
- workspace_intelligence_history: Version archive for Living Artifact model
- artifact_type enum for Sentinel filtering
- summary_snippet for fast UI previews
- Version tracking and access counting
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "j4k5l6m7n8o9"
down_revision = "h2i3j4k5l6m7"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Create the artifact_type enum
    artifact_type_enum = postgresql.ENUM(
        'RESEARCH',
        'CONVERSATION_INSIGHT',
        'TASK_STATE',
        'SYSTEM_VALIDATION',
        name='artifact_type_enum',
        create_type=True
    )
    artifact_type_enum.create(op.get_bind(), checkfirst=True)

    # 2. Create the main workspace_intelligence table
    op.create_table(
        'workspace_intelligence',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('space_id', postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column('agent_id', sa.String(100), nullable=False),
        sa.Column('key', sa.String(255), nullable=False, index=True),
        sa.Column(
            'artifact_type',
            postgresql.ENUM('RESEARCH', 'CONVERSATION_INSIGHT', 'TASK_STATE', 'SYSTEM_VALIDATION', name='artifact_type_enum', create_type=False),
            nullable=False,
            server_default='RESEARCH'
        ),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('summary_snippet', sa.Text(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('access_count', sa.Integer(), server_default='0', nullable=False),
        # Foreign key to organizations table
        sa.ForeignKeyConstraint(['space_id'], ['organizations.id'], ondelete='CASCADE'),
    )

    # 3. Create unique constraint: one key per space
    op.create_unique_constraint(
        'uq_workspace_intelligence_space_key',
        'workspace_intelligence',
        ['space_id', 'key']
    )

    # 4. Create indexes for efficient queries
    op.create_index(
        'ix_workspace_intelligence_space_agent',
        'workspace_intelligence',
        ['space_id', 'agent_id']
    )
    op.create_index(
        'ix_workspace_intelligence_access_count',
        'workspace_intelligence',
        ['access_count']
    )
    op.create_index(
        'ix_workspace_intelligence_artifact_type',
        'workspace_intelligence',
        ['space_id', 'artifact_type']
    )
    op.create_index(
        'ix_workspace_intelligence_created_at',
        'workspace_intelligence',
        ['space_id', 'created_at']
    )

    # 5. Create the history table for version archiving
    op.create_table(
        'workspace_intelligence_history',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('intelligence_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('space_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', sa.String(100), nullable=False),
        sa.Column('key', sa.String(255), nullable=False),
        sa.Column('artifact_type', postgresql.ENUM('RESEARCH', 'CONVERSATION_INSIGHT', 'TASK_STATE', 'SYSTEM_VALIDATION', name='artifact_type_enum', create_type=False), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('summary_snippet', sa.Text(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        # Foreign keys
        sa.ForeignKeyConstraint(['intelligence_id'], ['workspace_intelligence.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['space_id'], ['organizations.id'], ondelete='CASCADE'),
    )

    # 6. Create indexes for history lookups
    op.create_index(
        'ix_workspace_intelligence_history_intelligence_id',
        'workspace_intelligence_history',
        ['intelligence_id']
    )
    op.create_index(
        'ix_workspace_intelligence_history_space_key',
        'workspace_intelligence_history',
        ['space_id', 'key']
    )


def downgrade():
    # Drop history table and indexes
    op.drop_index('ix_workspace_intelligence_history_space_key', table_name='workspace_intelligence_history')
    op.drop_index('ix_workspace_intelligence_history_intelligence_id', table_name='workspace_intelligence_history')
    op.drop_table('workspace_intelligence_history')

    # Drop main table indexes
    op.drop_index('ix_workspace_intelligence_created_at', table_name='workspace_intelligence')
    op.drop_index('ix_workspace_intelligence_artifact_type', table_name='workspace_intelligence')
    op.drop_index('ix_workspace_intelligence_access_count', table_name='workspace_intelligence')
    op.drop_index('ix_workspace_intelligence_space_agent', table_name='workspace_intelligence')

    # Drop unique constraint
    op.drop_constraint('uq_workspace_intelligence_space_key', 'workspace_intelligence', type_='unique')

    # Drop main table
    op.drop_table('workspace_intelligence')

    # Drop enum type
    sa.Enum(name='artifact_type_enum').drop(op.get_bind(), checkfirst=True)
