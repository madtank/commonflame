"""add agent movement audit log

Revision ID: add_agent_movement_audit
Revises: f83f1698aa2b
Create Date: 2025-08-16

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'add_agent_movement_audit'
down_revision = '951dc48445c5'
branch_labels = None
depends_on = None


def upgrade():
    # Create agent_audit_logs table for security tracking
    op.create_table('agent_audit_logs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action', sa.String(50), nullable=False),  # 'move', 'pin', 'unpin', 'create', 'delete'
        sa.Column('from_org_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('to_org_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('reason', sa.String(255), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False, default=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['from_org_id'], ['organizations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['to_org_id'], ['organizations.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )

    # Create index for efficient querying
    op.create_index('idx_agent_audit_timestamp', 'agent_audit_logs', ['timestamp'])
    op.create_index('idx_agent_audit_agent_id', 'agent_audit_logs', ['agent_id'])
    op.create_index('idx_agent_audit_user_id', 'agent_audit_logs', ['user_id'])
    op.create_index('idx_agent_audit_action', 'agent_audit_logs', ['action'])

    # Add settings column to agents table for follow_user flag
    op.add_column('agents', sa.Column('settings', postgresql.JSON(), nullable=True))

    # Add database constraint to ensure agent.user_id is never null
    op.create_check_constraint(
        'ck_agents_user_id_not_null',
        'agents',
        'user_id IS NOT NULL'
    )

    # Add trigger to automatically log agent movements
    op.execute("""
        CREATE OR REPLACE FUNCTION log_agent_movement()
        RETURNS TRIGGER AS $$
        BEGIN
            IF OLD.org_id IS DISTINCT FROM NEW.org_id THEN
                INSERT INTO agent_audit_logs (
                    id, user_id, agent_id, action,
                    from_org_id, to_org_id, reason, success
                )
                VALUES (
                    gen_random_uuid(),
                    NEW.user_id,
                    NEW.id,
                    'move',
                    OLD.org_id,
                    NEW.org_id,
                    'Automatic logging via trigger',
                    true
                );
            END IF;

            IF OLD.pinned_to_org IS DISTINCT FROM NEW.pinned_to_org THEN
                INSERT INTO agent_audit_logs (
                    id, user_id, agent_id, action,
                    from_org_id, to_org_id, reason, success
                )
                VALUES (
                    gen_random_uuid(),
                    NEW.user_id,
                    NEW.id,
                    CASE
                        WHEN NEW.pinned_to_org IS NOT NULL THEN 'pin'
                        ELSE 'unpin'
                    END,
                    OLD.pinned_to_org,
                    NEW.pinned_to_org,
                    'Pin status changed',
                    true
                );
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER tr_log_agent_movement
        AFTER UPDATE ON agents
        FOR EACH ROW
        EXECUTE FUNCTION log_agent_movement();
    """)


def downgrade():
    # Drop trigger and function
    op.execute("DROP TRIGGER IF EXISTS tr_log_agent_movement ON agents")
    op.execute("DROP FUNCTION IF EXISTS log_agent_movement()")

    # Drop constraints
    op.drop_constraint('ck_agents_user_id_not_null', 'agents', type_='check')

    # Drop columns
    op.drop_column('agents', 'settings')

    # Drop indexes
    op.drop_index('idx_agent_audit_action', table_name='agent_audit_logs')
    op.drop_index('idx_agent_audit_user_id', table_name='agent_audit_logs')
    op.drop_index('idx_agent_audit_agent_id', table_name='agent_audit_logs')
    op.drop_index('idx_agent_audit_timestamp', table_name='agent_audit_logs')

    # Drop table
    op.drop_table('agent_audit_logs')
