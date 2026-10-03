"""AX-AGENT-MGMT-001 Phase 1: Agent management authorization schema

Adds:
- agents: owner_type, owner_user_id, owner_space_id, home_space_id,
  management_class, platform_managed, identity_locked, deletion_protected,
  global_state, created_by_user_id, created_by_agent_id, version
- agent_space_access: state, suspension_mode, suspend_reason_code,
  suspend_reason_text, attached/suspended/detached_by fields, version
- agent_space_overrides (new table)
- spaces: agent_policy JSONB
- space_memberships: ax_delegation_policy JSONB
- agent_management_proposals (new table)
- agent_management_approvals (new table)
- agent_management_audit (new table)
- agent_management_outbox (new table)
- Partial unique index for one-concierge-per-space invariant
- Backfills existing agents

Revision ID: sec02_agent_mgmt
Revises: sec01_msg_uid_null
Create Date: 2026-03-15

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = 'sec02_agent_mgmt'
down_revision = 'sec01_msg_uid_null'
branch_labels = None
depends_on = None


def upgrade():
    # ---------------------------------------------------------------
    # 1. agents table: new columns
    # ---------------------------------------------------------------
    op.add_column('agents', sa.Column(
        'owner_type', sa.String(10), nullable=False, server_default='user',
        comment='user | space | platform',
    ))
    op.add_column('agents', sa.Column(
        'owner_user_id', UUID(as_uuid=True),
        sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
        comment='Owner user (when owner_type=user)',
    ))
    op.add_column('agents', sa.Column(
        'owner_space_id', UUID(as_uuid=True),
        sa.ForeignKey('spaces.id', ondelete='CASCADE'), nullable=True,
        comment='Owner space (when owner_type=space)',
    ))
    op.add_column('agents', sa.Column(
        'home_space_id', UUID(as_uuid=True),
        sa.ForeignKey('spaces.id', ondelete='SET NULL'), nullable=True,
        comment='Home space for space-owned agents',
    ))
    op.add_column('agents', sa.Column(
        'management_class', sa.String(20), nullable=False, server_default='regular',
        comment='regular | concierge',
    ))
    op.add_column('agents', sa.Column(
        'platform_managed', sa.Boolean(), nullable=False, server_default='false',
    ))
    op.add_column('agents', sa.Column(
        'identity_locked', sa.Boolean(), nullable=False, server_default='false',
    ))
    op.add_column('agents', sa.Column(
        'deletion_protected', sa.Boolean(), nullable=False, server_default='false',
    ))
    op.add_column('agents', sa.Column(
        'global_state', sa.String(20), nullable=False, server_default='active',
        comment='active | disabled | archived',
    ))
    op.add_column('agents', sa.Column(
        'created_by_user_id', UUID(as_uuid=True),
        sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
    ))
    op.add_column('agents', sa.Column(
        'created_by_agent_id', UUID(as_uuid=True),
        sa.ForeignKey('agents.id', ondelete='SET NULL'), nullable=True,
    ))
    op.add_column('agents', sa.Column(
        'version', sa.Integer(), nullable=False, server_default='1',
    ))

    # Backfill: existing user-owned agents (only those with a user_id)
    op.execute("""
        UPDATE agents
        SET owner_user_id = user_id,
            owner_type = 'user',
            created_by_user_id = user_id,
            global_state = CASE
                WHEN status IN ('active') THEN 'active'
                WHEN status IN ('inactive', 'suspended', 'quarantined') THEN 'disabled'
                ELSE 'active'
            END
        WHERE origin != 'space_agent' AND user_id IS NOT NULL
    """)

    # Backfill: system/orphan agents with no user_id → platform-owned
    op.execute("""
        UPDATE agents
        SET owner_type = 'platform',
            platform_managed = true,
            global_state = CASE
                WHEN status IN ('active') THEN 'active'
                WHEN status IN ('inactive', 'suspended', 'quarantined') THEN 'disabled'
                ELSE 'active'
            END
        WHERE origin != 'space_agent' AND user_id IS NULL
    """)

    # Backfill: space agents → owner_type='space', management_class='concierge'
    op.execute("""
        UPDATE agents
        SET owner_type = 'space',
            owner_space_id = space_id,
            home_space_id = space_id,
            management_class = 'concierge',
            platform_managed = true,
            identity_locked = true,
            deletion_protected = true,
            global_state = CASE
                WHEN status IN ('active') THEN 'active'
                WHEN status IN ('inactive', 'suspended', 'quarantined') THEN 'disabled'
                ELSE 'active'
            END
        WHERE origin = 'space_agent'
    """)

    # Partial unique index: one non-archived concierge per space
    op.create_index(
        'ix_one_concierge_per_space',
        'agents',
        ['owner_space_id'],
        unique=True,
        postgresql_where=sa.text("management_class = 'concierge' AND global_state != 'archived'"),
    )

    # Check constraints
    op.execute("""
        ALTER TABLE agents ADD CONSTRAINT ck_agents_owner_type
        CHECK (owner_type IN ('user', 'space', 'platform'))
    """)
    op.execute("""
        ALTER TABLE agents ADD CONSTRAINT ck_agents_management_class
        CHECK (management_class IN ('regular', 'concierge'))
    """)
    op.execute("""
        ALTER TABLE agents ADD CONSTRAINT ck_agents_global_state
        CHECK (global_state IN ('active', 'disabled', 'archived'))
    """)
    # owner_type='user' requires owner_user_id
    op.execute("""
        ALTER TABLE agents ADD CONSTRAINT ck_agents_user_owner
        CHECK (owner_type != 'user' OR owner_user_id IS NOT NULL)
    """)
    # owner_type='space' requires owner_space_id and home_space_id
    op.execute("""
        ALTER TABLE agents ADD CONSTRAINT ck_agents_space_owner
        CHECK (owner_type != 'space' OR (owner_space_id IS NOT NULL AND home_space_id IS NOT NULL))
    """)

    # ---------------------------------------------------------------
    # 2. agent_space_access: attachment state columns
    # ---------------------------------------------------------------
    op.add_column('agent_space_access', sa.Column(
        'state', sa.String(20), nullable=False, server_default='active',
        comment='active | suspended | detached',
    ))
    op.add_column('agent_space_access', sa.Column(
        'suspension_mode', sa.String(30), nullable=True,
        comment='manual_admin | manual_owner | autonomous_safeguard',
    ))
    op.add_column('agent_space_access', sa.Column(
        'suspend_reason_code', sa.String(30), nullable=True,
    ))
    op.add_column('agent_space_access', sa.Column(
        'suspend_reason_text', sa.Text(), nullable=True,
    ))
    op.add_column('agent_space_access', sa.Column(
        'attached_by_user_id', UUID(as_uuid=True),
        sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
    ))
    op.add_column('agent_space_access', sa.Column(
        'attached_by_agent_id', UUID(as_uuid=True),
        sa.ForeignKey('agents.id', ondelete='SET NULL'), nullable=True,
    ))
    op.add_column('agent_space_access', sa.Column(
        'attached_at', sa.DateTime(timezone=True),
        server_default=sa.func.now(), nullable=True,
    ))
    op.add_column('agent_space_access', sa.Column(
        'suspended_by_user_id', UUID(as_uuid=True),
        sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
    ))
    op.add_column('agent_space_access', sa.Column(
        'suspended_by_agent_id', UUID(as_uuid=True),
        sa.ForeignKey('agents.id', ondelete='SET NULL'), nullable=True,
    ))
    op.add_column('agent_space_access', sa.Column(
        'suspended_at', sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column('agent_space_access', sa.Column(
        'detached_by_user_id', UUID(as_uuid=True),
        sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True,
    ))
    op.add_column('agent_space_access', sa.Column(
        'detached_by_agent_id', UUID(as_uuid=True),
        sa.ForeignKey('agents.id', ondelete='SET NULL'), nullable=True,
    ))
    op.add_column('agent_space_access', sa.Column(
        'detached_at', sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column('agent_space_access', sa.Column(
        'version', sa.Integer(), nullable=False, server_default='1',
    ))

    op.execute("""
        ALTER TABLE agent_space_access ADD CONSTRAINT ck_asa_state
        CHECK (state IN ('active', 'suspended', 'detached'))
    """)

    # ---------------------------------------------------------------
    # 3. agent_space_overrides (new table)
    # ---------------------------------------------------------------
    op.create_table(
        'agent_space_overrides',
        sa.Column('agent_id', UUID(as_uuid=True), sa.ForeignKey('agents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('space_id', UUID(as_uuid=True), sa.ForeignKey('spaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('display_name_override', sa.Text(), nullable=True),
        sa.Column('tool_allowlist_override', JSONB, nullable=True),
        sa.Column('routing_priority_override', sa.Integer(), nullable=True),
        sa.Column('visibility_override', JSONB, nullable=True),
        sa.Column('set_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('agent_id', 'space_id'),
    )

    # ---------------------------------------------------------------
    # 4. spaces: agent_policy JSONB
    # ---------------------------------------------------------------
    op.add_column('spaces', sa.Column(
        'agent_policy', JSONB, nullable=True, server_default=sa.text("'{}'::jsonb"),
        comment='Space-level agent management policy',
    ))

    # ---------------------------------------------------------------
    # 5. space_memberships: structured delegation policy
    # ---------------------------------------------------------------
    op.add_column('space_memberships', sa.Column(
        'ax_delegation_policy', JSONB, nullable=True,
        server_default=sa.text(
            "'{\"allow_basic_create\": true, \"allow_basic_update\": true, "
            "\"allow_sensitive_update\": false, \"allow_global_disable\": false}'::jsonb"
        ),
        comment='Per-member delegation policy for concierge actions',
    ))

    # ---------------------------------------------------------------
    # 6. agent_management_proposals (new table)
    # ---------------------------------------------------------------
    op.create_table(
        'agent_management_proposals',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('space_id', UUID(as_uuid=True), sa.ForeignKey('spaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('proposal_type', sa.String(50), nullable=False),
        sa.Column('status', sa.String(30), nullable=False, server_default='pending'),
        sa.Column('requested_by_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('requested_by_agent_id', UUID(as_uuid=True), sa.ForeignKey('agents.id', ondelete='SET NULL'), nullable=True),
        sa.Column('target_agent_id', UUID(as_uuid=True), sa.ForeignKey('agents.id', ondelete='SET NULL'), nullable=True),
        sa.Column('target_owner_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('target_space_id', UUID(as_uuid=True), sa.ForeignKey('spaces.id', ondelete='SET NULL'), nullable=True),
        sa.Column('source_space_id', UUID(as_uuid=True), sa.ForeignKey('spaces.id', ondelete='SET NULL'), nullable=True),
        sa.Column('destination_space_id', UUID(as_uuid=True), sa.ForeignKey('spaces.id', ondelete='SET NULL'), nullable=True),
        sa.Column('approval_requirements', JSONB, nullable=False),
        sa.Column('proposed_payload', JSONB, nullable=False),
        sa.Column('payload_hash', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('idempotency_key', sa.Text(), nullable=True, unique=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
    )
    op.create_index('ix_proposals_space', 'agent_management_proposals', ['space_id', 'created_at'])
    op.create_index('ix_proposals_target', 'agent_management_proposals', ['target_agent_id'])

    op.execute("""
        ALTER TABLE agent_management_proposals ADD CONSTRAINT ck_proposal_type
        CHECK (proposal_type IN (
            'create_user_agent', 'update_user_agent', 'disable_user_agent_global',
            'create_space_agent', 'update_space_agent',
            'attach_user_agent_to_space', 'move_user_agent_between_spaces'
        ))
    """)
    op.execute("""
        ALTER TABLE agent_management_proposals ADD CONSTRAINT ck_proposal_status
        CHECK (status IN (
            'pending', 'partially_approved', 'approved', 'rejected',
            'cancelled', 'expired', 'executed'
        ))
    """)

    # ---------------------------------------------------------------
    # 7. agent_management_approvals (new table)
    # ---------------------------------------------------------------
    op.create_table(
        'agent_management_approvals',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('proposal_id', UUID(as_uuid=True), sa.ForeignKey('agent_management_proposals.id', ondelete='CASCADE'), nullable=False),
        sa.Column('approver_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('approver_basis', sa.String(20), nullable=False),
        sa.Column('decision', sa.String(20), nullable=False),
        sa.Column('approval_patch', JSONB, nullable=True),
        sa.Column('approved_payload_hash', sa.Text(), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_approvals_proposal', 'agent_management_approvals', ['proposal_id'])

    op.execute("""
        ALTER TABLE agent_management_approvals ADD CONSTRAINT ck_approval_basis
        CHECK (approver_basis IN ('owner', 'space_admin', 'platform'))
    """)
    op.execute("""
        ALTER TABLE agent_management_approvals ADD CONSTRAINT ck_approval_decision
        CHECK (decision IN ('approved', 'rejected'))
    """)

    # ---------------------------------------------------------------
    # 8. agent_management_audit (new table)
    # ---------------------------------------------------------------
    op.create_table(
        'agent_management_audit',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('space_id', UUID(as_uuid=True), sa.ForeignKey('spaces.id', ondelete='CASCADE'), nullable=False),
        sa.Column('actor_user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('actor_agent_id', UUID(as_uuid=True), sa.ForeignKey('agents.id', ondelete='SET NULL'), nullable=True),
        sa.Column('actor_mode', sa.String(40), nullable=False),
        sa.Column('actor_space_role', sa.String(20), nullable=True),
        sa.Column('action', sa.String(50), nullable=False),
        sa.Column('target_agent_id', UUID(as_uuid=True), sa.ForeignKey('agents.id', ondelete='SET NULL'), nullable=True),
        sa.Column('target_owner_type', sa.String(10), nullable=True),
        sa.Column('proposal_id', UUID(as_uuid=True), nullable=True),
        sa.Column('request_fields', JSONB, nullable=True),
        sa.Column('old_state', JSONB, nullable=True),
        sa.Column('new_state', JSONB, nullable=True),
        sa.Column('policy_version', sa.String(20), nullable=False, server_default='v1'),
        sa.Column('auth_path', sa.String(50), nullable=False),
        sa.Column('approval_basis', JSONB, nullable=True),
        sa.Column('policy_decision', sa.String(10), nullable=False),
        sa.Column('denial_reason', sa.Text(), nullable=True),
        sa.Column('correlation_id', UUID(as_uuid=True), nullable=True),
        sa.Column('ip_address', sa.String(45), nullable=True),
    )
    op.create_index('ix_audit_space', 'agent_management_audit', ['space_id', 'created_at'])
    op.create_index('ix_audit_target', 'agent_management_audit', ['target_agent_id', 'created_at'])

    op.execute("""
        ALTER TABLE agent_management_audit ADD CONSTRAINT ck_audit_decision
        CHECK (policy_decision IN ('allowed', 'denied'))
    """)

    # ---------------------------------------------------------------
    # 9. agent_management_outbox (new table)
    # ---------------------------------------------------------------
    op.create_table(
        'agent_management_outbox',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('event_type', sa.String(50), nullable=False),
        sa.Column('payload', JSONB, nullable=False),
        sa.Column('space_id', UUID(as_uuid=True), nullable=False),
        sa.Column('correlation_id', UUID(as_uuid=True), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('ix_outbox_unpublished', 'agent_management_outbox', ['created_at'],
                     postgresql_where=sa.text('published_at IS NULL'))


def downgrade():
    op.drop_table('agent_management_outbox')
    op.drop_table('agent_management_audit')
    op.drop_table('agent_management_approvals')
    op.drop_table('agent_management_proposals')
    op.drop_table('agent_space_overrides')

    op.drop_column('space_memberships', 'ax_delegation_policy')
    op.drop_column('spaces', 'agent_policy')

    # agent_space_access: drop new columns
    op.execute("ALTER TABLE agent_space_access DROP CONSTRAINT IF EXISTS ck_asa_state")
    for col in ['state', 'suspension_mode', 'suspend_reason_code', 'suspend_reason_text',
                'attached_by_user_id', 'attached_by_agent_id', 'attached_at',
                'suspended_by_user_id', 'suspended_by_agent_id', 'suspended_at',
                'detached_by_user_id', 'detached_by_agent_id', 'detached_at', 'version']:
        op.drop_column('agent_space_access', col)

    # agents: drop new columns
    op.drop_index('ix_one_concierge_per_space', 'agents')
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_space_owner")
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_user_owner")
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_global_state")
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_management_class")
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_owner_type")
    for col in ['owner_type', 'owner_user_id', 'owner_space_id', 'home_space_id',
                'management_class', 'platform_managed', 'identity_locked',
                'deletion_protected', 'global_state', 'created_by_user_id',
                'created_by_agent_id', 'version']:
        op.drop_column('agents', col)
