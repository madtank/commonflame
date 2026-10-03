"""Rename org_id -> space_id across all tables + RLS policies

Revision ID: sr01_space_rename
Revises: merge_before_space_rename
Create Date: 2026-03-11

Column renames are instant metadata ops in Postgres (no table rewrite).
RLS policies are dropped and recreated with the new session variable name.

IMPORTANT: Never modify old migrations — they referenced org_id correctly at the time.
"""
from alembic import op


revision = "sr01_space_rename"
down_revision = "merge_before_space_rename"
branch_labels = None
depends_on = None

# ── Tables with org_id → space_id ────────────────────────────────
TABLES_WITH_ORG_ID = [
    "users",
    "agents",
    "messages",
    "tasks",
    "task_notes",
    "mentions",
    "feature_flags",
    "guardrail_configs",
    "guardrail_violations",
    "organization_memberships",
    "organization_invites",
    "conversation_cards",
    "user_settings",
    "message_feedback",
]

# ── Index renames (old_name, new_name, table) ────────────────────
# All use IF EXISTS to be safe against DB state variations
INDEX_RENAMES = [
    ("idx_tasks_org_id", "idx_tasks_space_id", "tasks"),
    ("idx_messages_org_id", "idx_messages_space_id", "messages"),
    ("idx_agents_org_id", "idx_agents_space_id", "agents"),
    ("idx_messages_org_created_at", "idx_messages_space_created_at", "messages"),
    ("idx_mentions_org_agent", "idx_mentions_space_agent", "mentions"),
    ("ix_feature_flags_org_id", "ix_feature_flags_space_id", "feature_flags"),
    ("idx_feedback_agent_org", "idx_feedback_agent_space", "message_feedback"),
    ("idx_org_invites_org_id", "idx_org_invites_space_id", "organization_invites"),
    ("idx_org_membership_org_id", "idx_org_membership_space_id", "organization_memberships"),
    ("idx_org_membership_user_org", "idx_org_membership_user_space", "organization_memberships"),
    ("idx_org_memberships_user_org", "idx_org_memberships_user_space", "organization_memberships"),
    # These are UNIQUE indexes (not constraints) in Postgres
    ("uq_agents_org_name", "uq_agents_space_name", "agents"),
    ("uq_flag_org_user", "uq_flag_space_user", "feature_flags"),
    ("uq_user_settings_user_org", "uq_user_settings_user_space", "user_settings"),
    # Additional indexes found in DB
    ("ix_conv_cards_org_activity", "ix_conv_cards_space_activity", "conversation_cards"),
    ("idx_guardrail_configs_org_id", "idx_guardrail_configs_space_id", "guardrail_configs"),
    ("idx_guardrail_violations_org_id", "idx_guardrail_violations_space_id", "guardrail_violations"),
    ("idx_task_notes_org_id", "idx_task_notes_space_id", "task_notes"),
    ("ix_user_settings_org_id", "ix_user_settings_space_id", "user_settings"),
    ("idx_users_org_id", "idx_users_space_id", "users"),
    ("ix_users_current_org_id", "ix_users_current_space_id", "users"),
    ("idx_users_org_ctx_version", "idx_users_space_ctx_version", "users"),
]

# ── Constraint renames (old_name, new_name, table) ───────────────
# Only actual DB constraints (not unique indexes)
CONSTRAINT_RENAMES = [
    ("unique_user_org_membership", "unique_user_space_membership", "organization_memberships"),
]


def upgrade():
    # ── 1. Column renames (instant metadata ops) ─────────────────
    for table in TABLES_WITH_ORG_ID:
        op.execute(f"ALTER TABLE {table} RENAME COLUMN org_id TO space_id")

    # Special columns
    op.execute("ALTER TABLE users RENAME COLUMN current_org_id TO current_space_id")
    op.execute("ALTER TABLE agents RENAME COLUMN pinned_to_org TO pinned_to_space")

    # ── 2. RLS policy recreation ─────────────────────────────────
    # Drop old policies (they reference org_id column and app.current_org_id var)
    op.execute("DROP POLICY IF EXISTS tasks_org_isolation ON tasks")
    op.execute("DROP POLICY IF EXISTS messages_org_isolation ON messages")
    op.execute("DROP POLICY IF EXISTS agents_org_isolation ON agents")
    op.execute("DROP POLICY IF EXISTS organizations_membership ON organizations")

    # Recreate with space_id column + app.current_space_id session var
    op.execute("""
        CREATE POLICY tasks_space_isolation ON tasks
        FOR ALL
        USING (space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid)
        WITH CHECK (space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid)
    """)
    op.execute("""
        CREATE POLICY messages_space_isolation ON messages
        FOR ALL
        USING (space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid)
        WITH CHECK (space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid)
    """)
    op.execute("""
        CREATE POLICY agents_space_isolation ON agents
        FOR ALL
        USING (space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid)
        WITH CHECK (space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid)
    """)
    op.execute("""
        CREATE POLICY organizations_membership ON organizations
        FOR ALL
        USING (
            id = NULLIF(current_setting('app.current_space_id', true), '')::uuid
            OR
            id IN (
                SELECT space_id FROM organization_memberships
                WHERE user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
        )
    """)

    # ── 3. Index renames ─────────────────────────────────────────
    for old_name, new_name, _table in INDEX_RENAMES:
        op.execute(f"ALTER INDEX IF EXISTS {old_name} RENAME TO {new_name}")

    # ── 4. Constraint renames ────────────────────────────────────
    for old_name, new_name, table in CONSTRAINT_RENAMES:
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {old_name} TO {new_name}")


def downgrade():
    # ── 1. Reverse constraint renames ────────────────────────────
    for old_name, new_name, table in CONSTRAINT_RENAMES:
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT {new_name} TO {old_name}")

    # ── 2. Reverse index renames ─────────────────────────────────
    for old_name, new_name, _table in INDEX_RENAMES:
        op.execute(f"ALTER INDEX IF EXISTS {new_name} RENAME TO {old_name}")

    # ── 3. RLS policy recreation (back to org_id) ────────────────
    op.execute("DROP POLICY IF EXISTS tasks_space_isolation ON tasks")
    op.execute("DROP POLICY IF EXISTS messages_space_isolation ON messages")
    op.execute("DROP POLICY IF EXISTS agents_space_isolation ON agents")
    op.execute("DROP POLICY IF EXISTS organizations_membership ON organizations")

    op.execute("""
        CREATE POLICY tasks_org_isolation ON tasks
        FOR ALL
        USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
    """)
    op.execute("""
        CREATE POLICY messages_org_isolation ON messages
        FOR ALL
        USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
    """)
    op.execute("""
        CREATE POLICY agents_org_isolation ON agents
        FOR ALL
        USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
    """)
    op.execute("""
        CREATE POLICY organizations_membership ON organizations
        FOR ALL
        USING (
            id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            OR
            id IN (
                SELECT org_id FROM organization_memberships
                WHERE user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
        )
    """)

    # ── 4. Reverse column renames ────────────────────────────────
    op.execute("ALTER TABLE agents RENAME COLUMN pinned_to_space TO pinned_to_org")
    op.execute("ALTER TABLE users RENAME COLUMN current_space_id TO current_org_id")

    for table in TABLES_WITH_ORG_ID:
        op.execute(f"ALTER TABLE {table} RENAME COLUMN space_id TO org_id")
