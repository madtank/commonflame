"""Rename organizations→spaces, organization_memberships→space_memberships,
organization_invites→space_invite_codes.  Add spaces.default_model.

Revision ID: sp01_rename_tables
Revises: pat01_add_agent_scope
Create Date: 2026-03-13

Table renames are instant metadata-only ops in PostgreSQL.
FK constraints auto-track renames — no manual FK updates needed.
RLS policies and trigger functions must be recreated with new table names.
"""
from alembic import op
import sqlalchemy as sa

revision: str = "sp01_rename_tables"
down_revision: str = "pat01_add_agent_scope"
branch_labels = None
depends_on = None

# RLS helpers (same as rls01)
_PRIV = "current_setting('app.is_privileged', true) = 'true'"
_SPACE = "space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid"


def upgrade():
    # ── 1. Rename tables ──────────────────────────────────────────
    op.rename_table("organizations", "spaces")
    op.rename_table("organization_memberships", "space_memberships")
    op.rename_table("organization_invites", "space_invite_codes")

    # ── 2. Add default_model column ───────────────────────────────
    op.add_column("spaces", sa.Column("default_model", sa.String(100), nullable=True))

    # ── 3. Rename indexes ─────────────────────────────────────────
    # sr01 already renamed org_id→space_id in index names, but they
    # still have "org" prefix.  Rename to match new table names.
    op.execute("ALTER INDEX IF EXISTS idx_org_invites_space_id RENAME TO idx_space_invite_codes_space_id")
    op.execute("ALTER INDEX IF EXISTS idx_org_invites_invite_code RENAME TO idx_space_invite_codes_invite_code")
    op.execute("ALTER INDEX IF EXISTS idx_org_invites_active_expires RENAME TO idx_space_invite_codes_active_expires")
    op.execute("ALTER INDEX IF EXISTS idx_org_membership_space_id RENAME TO idx_space_memberships_space_id")
    op.execute("ALTER INDEX IF EXISTS idx_org_membership_user_id RENAME TO idx_space_memberships_user_id")
    op.execute("ALTER INDEX IF EXISTS idx_org_membership_user_space RENAME TO idx_space_memberships_user_space")
    op.execute("ALTER INDEX IF EXISTS idx_org_memberships_user_space RENAME TO idx_space_memberships_user_space2")
    op.execute("ALTER INDEX IF EXISTS idx_org_memberships_org_user RENAME TO idx_space_memberships_space_user")
    op.execute("ALTER INDEX IF EXISTS idx_org_memberships_unique RENAME TO idx_space_memberships_unique")
    op.execute("ALTER INDEX IF EXISTS idx_organizations_slug RENAME TO idx_spaces_slug")

    # ── 4. Drop old RLS policies on renamed tables ────────────────
    op.execute("DROP POLICY IF EXISTS organizations_membership ON spaces")
    op.execute("DROP POLICY IF EXISTS org_memberships_space_isolation ON space_memberships")
    op.execute("DROP POLICY IF EXISTS org_invites_space_isolation ON space_invite_codes")

    # ── 5. Recreate RLS policies with new table names ─────────────

    # spaces — membership-based + privileged bypass
    op.execute(f"""
        CREATE POLICY spaces_membership ON spaces
        FOR ALL
        USING (
            {_PRIV}
            OR id = NULLIF(current_setting('app.current_space_id', true), '')::uuid
            OR id IN (
                SELECT space_id FROM space_memberships
                WHERE user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
        )
        WITH CHECK (
            {_PRIV}
            OR id = NULLIF(current_setting('app.current_space_id', true), '')::uuid
        )
    """)

    # space_memberships
    op.execute(f"""
        CREATE POLICY space_memberships_isolation ON space_memberships
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # space_invite_codes
    op.execute(f"""
        CREATE POLICY space_invite_codes_isolation ON space_invite_codes
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    # Ensure RLS is enabled + forced on renamed tables
    for table in ("spaces", "space_memberships", "space_invite_codes"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # ── 6. Update trigger functions to reference new table names ──
    # Only recreate check_enterprise_single_membership if the
    # organization_type column exists (legacy schema).  Databases
    # that never had this column can safely skip it.

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'spaces'
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
                        FROM spaces WHERE id = NEW.space_id;

                        IF target_org_type = 'enterprise' THEN
                            SELECT COUNT(*) INTO enterprise_count
                            FROM space_memberships om
                            JOIN spaces o ON om.space_id = o.id
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
                FROM spaces o
                LEFT JOIN space_memberships om ON o.id = om.space_id
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


def downgrade():
    # ── Reverse trigger functions ─────────────────────────────────
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

    # ── Drop new RLS policies ─────────────────────────────────────
    op.execute("DROP POLICY IF EXISTS space_invite_codes_isolation ON space_invite_codes")
    op.execute("DROP POLICY IF EXISTS space_memberships_isolation ON space_memberships")
    op.execute("DROP POLICY IF EXISTS spaces_membership ON spaces")

    # ── Drop default_model column (while table is still named spaces) ─
    op.drop_column("spaces", "default_model")

    # ── Reverse index renames ─────────────────────────────────────
    op.execute("ALTER INDEX IF EXISTS idx_spaces_slug RENAME TO idx_organizations_slug")
    op.execute("ALTER INDEX IF EXISTS idx_space_memberships_unique RENAME TO idx_org_memberships_unique")
    op.execute("ALTER INDEX IF EXISTS idx_space_memberships_space_user RENAME TO idx_org_memberships_org_user")
    op.execute("ALTER INDEX IF EXISTS idx_space_memberships_user_space2 RENAME TO idx_org_memberships_user_space")
    op.execute("ALTER INDEX IF EXISTS idx_space_memberships_user_space RENAME TO idx_org_membership_user_space")
    op.execute("ALTER INDEX IF EXISTS idx_space_memberships_user_id RENAME TO idx_org_membership_user_id")
    op.execute("ALTER INDEX IF EXISTS idx_space_memberships_space_id RENAME TO idx_org_membership_space_id")
    op.execute("ALTER INDEX IF EXISTS idx_space_invite_codes_active_expires RENAME TO idx_org_invites_active_expires")
    op.execute("ALTER INDEX IF EXISTS idx_space_invite_codes_invite_code RENAME TO idx_org_invites_invite_code")
    op.execute("ALTER INDEX IF EXISTS idx_space_invite_codes_space_id RENAME TO idx_org_invites_space_id")

    # ── Rename tables back ────────────────────────────────────────
    op.rename_table("space_invite_codes", "organization_invites")
    op.rename_table("space_memberships", "organization_memberships")
    op.rename_table("spaces", "organizations")

    # ── Recreate old RLS policies ─────────────────────────────────
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

    op.execute(f"""
        CREATE POLICY org_memberships_space_isolation ON organization_memberships
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    op.execute(f"""
        CREATE POLICY org_invites_space_isolation ON organization_invites
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """)

    for table in ("organizations", "organization_memberships", "organization_invites"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
