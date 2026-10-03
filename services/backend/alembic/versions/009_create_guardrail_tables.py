"""Create guardrail tables

Revision ID: 009_guardrail_tables
Revises: 008_add_performance_indexes
Create Date: 2025-07-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '009_guardrail_tables'
down_revision = '008_add_performance_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create guardrail violations and configs tables"""

    # Create guardrail_violations table
    op.create_table('guardrail_violations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=True),

        # Violation Details
        sa.Column('violation_type', sa.String(50), nullable=False),
        sa.Column('severity', sa.String(20), nullable=False, default='medium'),
        sa.Column('description', sa.Text(), nullable=False),

        # Context Information
        sa.Column('endpoint', sa.String(100), nullable=False),
        sa.Column('method', sa.String(10), nullable=False),
        sa.Column('content_type', sa.String(20), nullable=False),

        # Original Content (for audit/analysis)
        sa.Column('original_content', sa.Text(), nullable=True),
        sa.Column('sanitized_content', sa.Text(), nullable=True),

        # Portkey Response Data
        sa.Column('portkey_response', postgresql.JSON(), nullable=True),
        sa.Column('portkey_status_code', sa.Integer(), nullable=True),

        # Detection Metadata
        sa.Column('detection_rules', postgresql.JSON(), nullable=True),
        sa.Column('confidence_score', sa.Integer(), nullable=True),

        # Admin Actions
        sa.Column('resolved', sa.Boolean(), default=False),
        sa.Column('resolved_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),

        # Additional Security Metadata
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.String(500), nullable=True),
        sa.Column('session_id', sa.String(255), nullable=True),

        # Multi-tenant Security
        sa.Column('tenant_isolation_level', sa.String(20), default='organization'),
        sa.Column('cross_tenant_risk', sa.Boolean(), default=False),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ondelete='SET NULL')
    )

    # Create guardrail_configs table
    op.create_table('guardrail_configs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),

        # Configuration Details
        sa.Column('config_name', sa.String(100), nullable=False),
        sa.Column('config_type', sa.String(50), nullable=False),
        sa.Column('endpoint_pattern', sa.String(200), nullable=False),

        # Portkey Configuration
        sa.Column('portkey_config', postgresql.JSON(), nullable=False),
        sa.Column('enabled', sa.Boolean(), default=True),

        # Rule Priority and Actions
        sa.Column('priority', sa.Integer(), default=100),
        sa.Column('action_on_violation', sa.String(20), default='log'),
        sa.Column('fallback_config', postgresql.JSON(), nullable=True),

        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('created_by', postgresql.UUID(as_uuid=True), nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL')
    )

    # Create indexes for performance
    op.create_index('idx_guardrail_violations_org_created', 'guardrail_violations', ['org_id', 'created_at'])
    op.create_index('idx_guardrail_violations_user_resolved', 'guardrail_violations', ['user_id', 'resolved', 'created_at'])
    op.create_index('idx_guardrail_violations_severity', 'guardrail_violations', ['severity', 'created_at'])
    op.create_index('idx_guardrail_violations_type', 'guardrail_violations', ['violation_type', 'created_at'])
    op.create_index('idx_guardrail_configs_org_enabled', 'guardrail_configs', ['org_id', 'enabled'])


def downgrade() -> None:
    """Drop guardrail tables"""
    op.drop_index('idx_guardrail_configs_org_enabled')
    op.drop_index('idx_guardrail_violations_type')
    op.drop_index('idx_guardrail_violations_severity')
    op.drop_index('idx_guardrail_violations_user_resolved')
    op.drop_index('idx_guardrail_violations_org_created')
    op.drop_table('guardrail_configs')
    op.drop_table('guardrail_violations')
