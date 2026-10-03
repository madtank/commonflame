"""Backfill Space Agent records for all existing organizations

Creates a Space Agent for every org that doesn't have one.
Also relaxes unique constraints that block platform-owned agents:
- agents_user_id_name_unique: excluded for origin='space_agent'
- idx_agents_name_lower_unique: excluded for origin='space_agent'

Revision ID: sa01_backfill
Revises: merge_2026_03_08
Create Date: 2026-03-09
"""

from alembic import op
import sqlalchemy as sa

revision = "sa01_backfill"
down_revision = "merge_2026_03_08"
branch_labels = None
depends_on = None

SYSTEM_ORG_ID = "00000000-0000-0000-0000-000000000000"
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade():
    # Ensure the reserved platform principal exists even on databases that
    # never ran the older internal-system-agents branch.
    op.execute(sa.text(f"""
        INSERT INTO organizations (
            id, name, slug, visibility, tier, is_archived, is_internal,
            show_member_list_to_guests, created_at, updated_at
        )
        VALUES (
            '{SYSTEM_ORG_ID}',
            '__system__',
            '__system__',
            'private',
            'admin',
            false,
            true,
            false,
            NOW(),
            NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            name = '__system__',
            slug = '__system__',
            visibility = 'private',
            tier = 'admin',
            is_archived = false,
            is_internal = true,
            show_member_list_to_guests = false,
            updated_at = NOW();
    """))
    op.execute(sa.text(f"""
        INSERT INTO users (id, org_id, email, password_hash, active, role, auth_provider, created_at, updated_at)
        VALUES (
            '{SYSTEM_USER_ID}',
            '{SYSTEM_ORG_ID}',
            'system@internal.ax-platform',
            '$2b$12$IMPOSSIBLE.HASH.NEVER.MATCHES.ANYTHING.SYSTEM.USER.NO.LOGIN',
            true,
            'user',
            'system',
            NOW(),
            NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            org_id = '{SYSTEM_ORG_ID}',
            email = 'system@internal.ax-platform',
            password_hash = '$2b$12$IMPOSSIBLE.HASH.NEVER.MATCHES.ANYTHING.SYSTEM.USER.NO.LOGIN',
            auth_provider = 'system',
            updated_at = NOW();
    """))

    # 1. Drop old constraints that block multiple "Space Agent" records
    op.execute(sa.text(
        "ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_user_id_name_unique;"
    ))
    op.execute(sa.text(
        "DROP INDEX IF EXISTS idx_agents_name_lower_unique;"
    ))

    # 2. Recreate with exclusion for space_agent origin
    op.execute(sa.text("""
        CREATE UNIQUE INDEX agents_user_id_name_unique
        ON agents (user_id, name)
        WHERE origin IS DISTINCT FROM 'space_agent';
    """))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX idx_agents_name_lower_unique
        ON agents (lower(name))
        WHERE lower(name) <> 'chirpy'
          AND origin IS DISTINCT FROM 'space_agent';
    """))

    # 3. Backfill Space Agent for every org without one
    op.execute(sa.text(f"""
        WITH new_agents AS (
            INSERT INTO agents (
                id, user_id, org_id, name, description, origin, agent_type,
                status, is_internal, webhook_verified,
                web_browsing_enabled, web_fetch_enabled, brave_search_enabled, image_gen_enabled,
                model, ax_mcp_enabled, enabled_tools, template_type,
                visibility_level, space_locked, created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                '{SYSTEM_USER_ID}'::uuid,
                o.id,
                'Space Agent',
                'Space Agent for ' || o.name,
                'space_agent',
                'space_agent',
                'active',
                false,
                false,
                false,
                false,
                false,
                false,
                'us.anthropic.claude-3-5-haiku-20241022-v1:0',
                true,
                '{{"ax_mcp": true}}'::jsonb,
                'ax_agent',
                'private',
                true,
                NOW(),
                NOW()
            FROM organizations o
            WHERE o.space_agent_id IS NULL
              AND o.id != '00000000-0000-0000-0000-000000000000'::uuid
              AND o.is_internal = false
            ON CONFLICT (org_id, name) DO NOTHING
            RETURNING id, org_id
        )
        UPDATE organizations
        SET space_agent_id = new_agents.id, updated_at = NOW()
        FROM new_agents
        WHERE organizations.id = new_agents.org_id;
    """))

    # 4. Link any pre-existing space agents that were manually inserted
    op.execute(sa.text("""
        UPDATE organizations o
        SET space_agent_id = a.id, updated_at = NOW()
        FROM agents a
        WHERE a.org_id = o.id
          AND a.origin = 'space_agent'
          AND o.space_agent_id IS NULL;
    """))


def downgrade():
    # Unlink space agents from orgs
    op.execute(sa.text("""
        UPDATE organizations
        SET space_agent_id = NULL, updated_at = NOW()
        WHERE space_agent_id IN (
            SELECT id FROM agents WHERE origin = 'space_agent'
        );
    """))
    # Delete backfilled space agents (system-owned only)
    op.execute(sa.text("""
        DELETE FROM agents
        WHERE origin = 'space_agent'
          AND user_id = :system_user_id::uuid;
    """).bindparams(system_user_id=SYSTEM_USER_ID))

    # Restore original constraints (without space_agent exclusion)
    op.execute(sa.text("DROP INDEX IF EXISTS agents_user_id_name_unique;"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_agents_name_lower_unique;"))
    op.execute(sa.text("""
        ALTER TABLE agents ADD CONSTRAINT agents_user_id_name_unique
        UNIQUE (user_id, name);
    """))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX idx_agents_name_lower_unique
        ON agents (lower(name))
        WHERE lower(name) <> 'chirpy';
    """))
