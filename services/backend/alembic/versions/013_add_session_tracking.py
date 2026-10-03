"""Add session tracking tables

Revision ID: 013_add_session_tracking
Revises: 012_add_hot_message_index
Create Date: 2025-08-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = '013_add_session_tracking'
down_revision = '011_add_posted_by_agent_id'
branch_labels = None
depends_on = None

def upgrade():
    # Create session tracking table
    op.create_table('mcp_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('session_id', sa.String(100), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('agent_name', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('terminated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('state', sa.String(20), nullable=False, server_default='open'),
        sa.Column('resurrected', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('resurrection_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('client_info', sa.JSON(), nullable=True),  # Store user agent, IP, etc
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    )

    # Create indexes for efficient querying
    op.create_index('idx_mcp_sessions_session_id', 'mcp_sessions', ['session_id'])
    op.create_index('idx_mcp_sessions_user_agent', 'mcp_sessions', ['user_id', 'agent_name'])
    op.create_index('idx_mcp_sessions_created_at', 'mcp_sessions', ['created_at'])
    op.create_index('idx_mcp_sessions_resurrected', 'mcp_sessions', ['resurrected'])

    # Create tool calls tracking table
    op.create_table('mcp_tool_calls',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('session_id', sa.String(100), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('tool_name', sa.String(100), nullable=False),
        sa.Column('method', sa.String(100), nullable=True),
        sa.Column('action', sa.String(50), nullable=True),
        sa.Column('arguments', sa.JSON(), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error', sa.JSON(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('idempotency_key', sa.String(100), nullable=True),
        sa.Column('was_resurrected_session', sa.Boolean(), nullable=False, server_default='false'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
    )

    # Create indexes for tool calls
    op.create_index('idx_mcp_tool_calls_session_id', 'mcp_tool_calls', ['session_id'])
    op.create_index('idx_mcp_tool_calls_tool_name', 'mcp_tool_calls', ['tool_name'])
    op.create_index('idx_mcp_tool_calls_created_at', 'mcp_tool_calls', ['created_at'])
    op.create_index('idx_mcp_tool_calls_idempotency', 'mcp_tool_calls', ['idempotency_key'])
    op.create_index('idx_mcp_tool_calls_resurrected', 'mcp_tool_calls', ['was_resurrected_session'])

    # Add RLS policies
    op.execute("""
        ALTER TABLE mcp_sessions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mcp_tool_calls ENABLE ROW LEVEL SECURITY;

        -- Users can see their own sessions
        CREATE POLICY mcp_sessions_user_policy ON mcp_sessions
            FOR ALL
            USING (user_id = current_setting('app.user_id')::uuid);

        -- Users can see their own tool calls
        CREATE POLICY mcp_tool_calls_user_policy ON mcp_tool_calls
            FOR ALL
            USING (user_id = current_setting('app.user_id')::uuid);
    """)

def downgrade():
    op.drop_table('mcp_tool_calls')
    op.drop_table('mcp_sessions')
