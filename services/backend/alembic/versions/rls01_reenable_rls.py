"""Re-enable Row Level Security on all tenant-scoped tables

Revision ID: rls01_reenable_rls
Revises: sr01_space_rename
Create Date: 2026-03-11

Reverses z9y8x7w6v5u4_disable_rls_temporarily.py and extends coverage
to all 15 tenant-scoped tables. Existing 4 policies are recreated with
an is_privileged bypass clause for SystemSession/AdminSession.

BACKOUT: `alembic downgrade rls01_reenable_rls-1` or `scripts/backout_rls.sh`
"""
from alembic import op

revision = "rls01_reenable_rls"
down_revision = "sr01_space_rename"
branch_labels = None
depends_on = None

# ── All 15 tenant-scoped tables ─────────────────────────────────
ALL_RLS_TABLES = [
    "tasks",
    "messages",
    "agents",
    "organizations",
    "task_notes",
    "mentions",
    "feature_flags",
    "guardrail_configs",
    "guardrail_violations",
    "conversation_cards",
    "user_settings",
    "message_feedback",
    "organization_memberships",
    "organization_invites",
    "users",
]

# Privileged bypass: SystemSession, AdminSession, background tasks
_PRIV = "current_setting('app.is_privileged', true) = 'true'"

# Standard space isolation expression
_SPACE = "space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid"


def upgrade():
    # ── 0. Ensure the active application role has DML rights on public tables ──
    # Local/dev databases often use axdev_app, while staging/prod may run under a
    # different application role such as ax_staging. Prefer axdev_app when it
    # exists for backward compatibility, otherwise fall back to current_user.
    op.execute("""
        DO $$
        DECLARE
            target_role text;
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'axdev_app') THEN
                target_role := 'axdev_app';
            ELSE
                target_role := current_user;
            END IF;

            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I',
                target_role
            );
            EXECUTE format(
                'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
                target_role
            );
        END $$;
    """)

    # ── 1. Drop existing 4 policies (they lack is_privileged) ────
    op.execute("DROP POLICY IF EXISTS tasks_space_isolation ON tasks")
    op.execute("DROP POLICY IF EXISTS messages_space_isolation ON messages")
    op.execute("DROP POLICY IF EXISTS agents_space_isolation ON agents")
    op.execute("DROP POLICY IF EXISTS organizations_membership ON organizations")

    # ── 2. Recreate 4 existing policies WITH is_privileged bypass ─

    # tasks
    op.execute(f"""
        CREATE POLICY tasks_space_isolation ON tasks
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # messages
    op.execute(f"""
        CREATE POLICY messages_space_isolation ON messages
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # agents
    op.execute(f"""
        CREATE POLICY agents_space_isolation ON agents
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # organizations — membership-based + privileged bypass
    op.execute(f"""
        CREATE POLICY organizations_membership ON organizations
        FOR ALL
        USING (
            {_PRIV}
            OR id = NULLIF(current_setting('app.current_space_id', true), '')::uuid
            OR id IN (
                SELECT space_id FROM organization_memberships
                WHERE user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
        )
        WITH CHECK (
            {_PRIV}
            OR id = NULLIF(current_setting('app.current_space_id', true), '')::uuid
        )
    """)

    # ── 3. Create 11 NEW policies ─────────────────────────────────

    # task_notes
    op.execute(f"""
        CREATE POLICY task_notes_space_isolation ON task_notes
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # mentions
    op.execute(f"""
        CREATE POLICY mentions_space_isolation ON mentions
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # feature_flags — global flags (space_id IS NULL) visible to all
    op.execute(f"""
        CREATE POLICY feature_flags_space_isolation ON feature_flags
        FOR ALL
        USING (
            {_PRIV}
            OR space_id IS NULL
            OR {_SPACE}
        )
        WITH CHECK (
            {_PRIV}
            OR space_id IS NULL
            OR {_SPACE}
        )
    """)

    # guardrail_configs
    op.execute(f"""
        CREATE POLICY guardrail_configs_space_isolation ON guardrail_configs
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # guardrail_violations
    op.execute(f"""
        CREATE POLICY guardrail_violations_space_isolation ON guardrail_violations
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # conversation_cards
    op.execute(f"""
        CREATE POLICY conversation_cards_space_isolation ON conversation_cards
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # user_settings
    op.execute(f"""
        CREATE POLICY user_settings_space_isolation ON user_settings
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # message_feedback
    op.execute(f"""
        CREATE POLICY message_feedback_space_isolation ON message_feedback
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # organization_memberships
    op.execute(f"""
        CREATE POLICY org_memberships_space_isolation ON organization_memberships
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # organization_invites
    op.execute(f"""
        CREATE POLICY org_invites_space_isolation ON organization_invites
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # users
    op.execute(f"""
        CREATE POLICY users_space_isolation ON users
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # ── 4. Fix trigger functions that still reference org_id ─────
    # These were missed in the sr01_space_rename migration.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'organizations'
                  AND column_name = 'organization_type'
            ) THEN
                EXECUTE $fn$
                    CREATE OR REPLACE FUNCTION check_enterprise_single_membership()
                    RETURNS TRIGGER AS $body$
                    DECLARE
                        enterprise_count INTEGER;
                        target_org_type text;
                    BEGIN
                        SELECT organization_type::text INTO target_org_type
                        FROM organizations WHERE id = NEW.space_id;

                        IF target_org_type = 'enterprise' THEN
                            SELECT COUNT(*) INTO enterprise_count
                            FROM organization_memberships om
                            JOIN organizations o ON om.space_id = o.id
                            WHERE om.user_id = NEW.user_id
                              AND o.organization_type::text = 'enterprise'
                              AND om.space_id != NEW.space_id;

                            IF enterprise_count > 0 THEN
                                RAISE EXCEPTION 'User can only belong to one enterprise organization.'
                                USING ERRCODE = 'unique_violation';
                            END IF;
                        END IF;
                        RETURN NEW;
                    END;
                    $body$ LANGUAGE plpgsql;
                $fn$;
            END IF;
        END $$;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION validate_private_workspace()
        RETURNS TRIGGER AS $$
        DECLARE
            member_count INTEGER;
            org_visibility VARCHAR;
        BEGIN
            IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
                SELECT o.visibility, COUNT(om.user_id)
                INTO org_visibility, member_count
                FROM organizations o
                LEFT JOIN organization_memberships om ON o.id = om.space_id
                WHERE o.id = NEW.space_id
                GROUP BY o.visibility;

                IF org_visibility = 'private' AND member_count >= 1 THEN
                    RAISE EXCEPTION 'Private workspaces can only have one member. Use invite_only for team workspaces.';
                END IF;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── 5. Enable + Force RLS on all 15 tables ───────────────────
    for table in ALL_RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade():
    # ── 0. Restore old trigger functions (with org_id) ───────────
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'organizations'
                  AND column_name = 'organization_type'
            ) THEN
                EXECUTE $fn$
                    CREATE OR REPLACE FUNCTION check_enterprise_single_membership()
                    RETURNS TRIGGER AS $body$
                    DECLARE
                        enterprise_count INTEGER;
                        target_org_type text;
                    BEGIN
                        SELECT organization_type::text INTO target_org_type
                        FROM organizations WHERE id = NEW.org_id;

                        IF target_org_type = 'enterprise' THEN
                            SELECT COUNT(*) INTO enterprise_count
                            FROM organization_memberships om
                            JOIN organizations o ON om.org_id = o.id
                            WHERE om.user_id = NEW.user_id
                              AND o.organization_type::text = 'enterprise'
                              AND om.org_id != NEW.org_id;

                            IF enterprise_count > 0 THEN
                                RAISE EXCEPTION 'User can only belong to one enterprise organization.'
                                USING ERRCODE = 'unique_violation';
                            END IF;
                        END IF;
                        RETURN NEW;
                    END;
                    $body$ LANGUAGE plpgsql;
                $fn$;
            END IF;
        END $$;
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION validate_private_workspace()
        RETURNS TRIGGER AS $$
        DECLARE
            member_count INTEGER;
            org_visibility VARCHAR;
        BEGIN
            IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
                SELECT o.visibility, COUNT(om.user_id)
                INTO org_visibility, member_count
                FROM organizations o
                LEFT JOIN organization_memberships om ON o.id = om.org_id
                WHERE o.id = NEW.org_id
                GROUP BY o.visibility;

                IF org_visibility = 'private' AND member_count >= 1 THEN
                    RAISE EXCEPTION 'Private workspaces can only have one member. Use invite_only for team workspaces.';
                END IF;
            END IF;
            RETURN COALESCE(NEW, OLD);
        END;
        $$ LANGUAGE plpgsql;
    """)

    # ── 1. Disable RLS on all 15 tables ──────────────────────────
    for table in ALL_RLS_TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # ── 2. Drop the 11 NEW policies ─────────────────────────────
    op.execute("DROP POLICY IF EXISTS task_notes_space_isolation ON task_notes")
    op.execute("DROP POLICY IF EXISTS mentions_space_isolation ON mentions")
    op.execute("DROP POLICY IF EXISTS feature_flags_space_isolation ON feature_flags")
    op.execute("DROP POLICY IF EXISTS guardrail_configs_space_isolation ON guardrail_configs")
    op.execute("DROP POLICY IF EXISTS guardrail_violations_space_isolation ON guardrail_violations")
    op.execute("DROP POLICY IF EXISTS conversation_cards_space_isolation ON conversation_cards")
    op.execute("DROP POLICY IF EXISTS user_settings_space_isolation ON user_settings")
    op.execute("DROP POLICY IF EXISTS message_feedback_space_isolation ON message_feedback")
    op.execute("DROP POLICY IF EXISTS org_memberships_space_isolation ON organization_memberships")
    op.execute("DROP POLICY IF EXISTS org_invites_space_isolation ON organization_invites")
    op.execute("DROP POLICY IF EXISTS users_space_isolation ON users")

    # ── 3. Recreate original 4 policies WITHOUT is_privileged ────
    #    (restores to the state from sr01_space_rename)
    op.execute("DROP POLICY IF EXISTS tasks_space_isolation ON tasks")
    op.execute("DROP POLICY IF EXISTS messages_space_isolation ON messages")
    op.execute("DROP POLICY IF EXISTS agents_space_isolation ON agents")
    op.execute("DROP POLICY IF EXISTS organizations_membership ON organizations")

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
