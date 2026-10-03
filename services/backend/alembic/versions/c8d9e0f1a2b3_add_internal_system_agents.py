"""add_internal_system_agents

Revision ID: c8d9e0f1a2b3
Revises: 288aecb655e7
Create Date: 2025-12-14 21:00:00.000000

Adds infrastructure for invisible internal system agents:
- is_internal flag on agents and organizations
- internal_type field for system agent categorization
- System org and user for owning system agents
- AI Validator system agent
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c8d9e0f1a2b3"
down_revision = "288aecb655e7"
branch_labels = None
depends_on = None

# System UUIDs (all zeros pattern for easy identification)
SYSTEM_ORG_ID = "00000000-0000-0000-0000-000000000000"
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"


def upgrade():
    # 1. Add is_internal and internal_type to agents table
    op.add_column(
        "agents",
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "agents",
        sa.Column("internal_type", sa.String(50), nullable=True),
    )

    # 2. Add is_internal to organizations table
    op.add_column(
        "organizations",
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default="false"),
    )

    # 3. Create index for fast internal agent lookup
    op.create_index(
        "idx_agents_internal",
        "agents",
        ["is_internal", "internal_type"],
        postgresql_where=sa.text("is_internal = true"),
    )

    # 4. Create or update system organization (hidden from all queries)
    # Use upsert pattern to handle existing System org
    op.execute(f"""
        INSERT INTO organizations (id, name, slug, is_internal, created_at, updated_at)
        VALUES (
            '{SYSTEM_ORG_ID}',
            '__system__',
            '__system__',
            true,
            NOW(),
            NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            name = '__system__',
            slug = '__system__',
            is_internal = true,
            updated_at = NOW();
    """)

    # 5. Create system user (cannot login, only owns system agents)
    # Note: org_id must reference the system org we just created
    # SECURITY: Use cryptographically impossible bcrypt hash (no plaintext ever matches)
    op.execute(f"""
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
    """)

    # 6. Create organization membership for system user
    op.execute(f"""
        INSERT INTO organization_memberships (user_id, org_id, role, created_at)
        VALUES (
            '{SYSTEM_USER_ID}',
            '{SYSTEM_ORG_ID}',
            'admin',
            NOW()
        )
        ON CONFLICT (user_id, org_id) DO NOTHING;
    """)

    # 7. Create AI Validator system agent
    op.execute(f"""
        INSERT INTO agents (
            id, name, description, user_id, org_id,
            agent_type, status, is_internal, internal_type,
            created_at, updated_at
        )
        VALUES (
            gen_random_uuid(),
            '__ai_validator__',
            'Internal system agent for AI content validation and reactions. Not visible to users.',
            '{SYSTEM_USER_ID}',
            '{SYSTEM_ORG_ID}',
            'system',
            'active',
            true,
            'ai_validator',
            NOW(),
            NOW()
        );
    """)


def downgrade():
    # Remove AI Validator agent
    op.execute("DELETE FROM agents WHERE internal_type = 'ai_validator';")

    # Remove system user membership
    op.execute(f"DELETE FROM organization_memberships WHERE user_id = '{SYSTEM_USER_ID}';")

    # Remove system user
    op.execute(f"DELETE FROM users WHERE id = '{SYSTEM_USER_ID}';")

    # Remove system org
    op.execute(f"DELETE FROM organizations WHERE id = '{SYSTEM_ORG_ID}';")

    # Drop index
    op.drop_index("idx_agents_internal", table_name="agents")

    # Drop columns
    op.drop_column("organizations", "is_internal")
    op.drop_column("agents", "internal_type")
    op.drop_column("agents", "is_internal")
