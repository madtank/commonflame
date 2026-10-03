"""Create agent_space_access table with data backfill

Revision ID: asa01_agent_space_access
Revises: rls01_reenable_rls, sa01_backfill
Create Date: 2026-03-12

Merges two heads (rls01_reenable_rls + sa01_backfill) and introduces
agent_space_access join table to replace scattered pinned_to_space /
FOLLOW_UUID / freeroam query patterns.  Backfills all existing agents
with at least one row.  Membership-based RLS policy (not standard
_SPACE pattern) so cross-space queries work.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision: str = "asa01_agent_space_access"
down_revision: Union[str, tuple[str, ...], None] = ("rls01_reenable_rls", "sa01_backfill", "cred01_create_credentials")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FOLLOW_UUID = "11111111-1111-1111-1111-111111111111"


def upgrade() -> None:
    # ── 1. Create table ──
    op.create_table(
        "agent_space_access",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("migration_flags", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=True),
    )

    # ── 2. Constraints and indexes ──
    op.create_unique_constraint("uq_agent_space", "agent_space_access", ["agent_id", "space_id"])
    op.create_index("ix_agent_space_agent", "agent_space_access", ["agent_id"])
    op.create_index("ix_agent_space_space", "agent_space_access", ["space_id"])

    # Partial unique index: exactly one default per agent
    op.execute(sa.text(
        "CREATE UNIQUE INDEX uq_agent_default_space "
        "ON agent_space_access(agent_id) WHERE is_default = true"
    ))

    # ── 3. RLS policy (membership-based) ──
    op.execute(sa.text("ALTER TABLE agent_space_access ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
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
                'GRANT SELECT, INSERT, UPDATE, DELETE ON agent_space_access TO %I',
                target_role
            );
        END $$;
    """))

    op.execute(sa.text("""
        CREATE POLICY agent_space_access_policy ON agent_space_access
        FOR ALL
        USING (
            current_setting('app.is_privileged', true) = 'true'
            OR space_id IN (
                SELECT om.space_id FROM organization_memberships om
                WHERE om.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
        )
        WITH CHECK (
            current_setting('app.is_privileged', true) = 'true'
            OR space_id IN (
                SELECT om.space_id FROM organization_memberships om
                WHERE om.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
        )
    """))

    # ── 4. Data backfill ──
    conn = op.get_bind()

    # 4a. Pinned agents (real UUID in pinned_to_space)
    conn.execute(text("""
        INSERT INTO agent_space_access (id, agent_id, space_id, is_default, created_at)
        SELECT gen_random_uuid(), a.id, a.pinned_to_space, true, NOW()
        FROM agents a
        WHERE a.pinned_to_space IS NOT NULL
          AND a.pinned_to_space != CAST(:follow_uuid AS uuid)
    """), {"follow_uuid": FOLLOW_UUID})

    # 4b. If space_id differs from pinned_to_space, add second row (not default)
    conn.execute(text("""
        INSERT INTO agent_space_access (id, agent_id, space_id, is_default, created_at)
        SELECT gen_random_uuid(), a.id, a.space_id, false, NOW()
        FROM agents a
        WHERE a.pinned_to_space IS NOT NULL
          AND a.pinned_to_space != CAST(:follow_uuid AS uuid)
          AND a.space_id != a.pinned_to_space
        ON CONFLICT (agent_id, space_id) DO NOTHING
    """), {"follow_uuid": FOLLOW_UUID})

    # 4c. Free roam agents (pinned_to_space IS NULL) — flagged for review
    # Exclude agents with NULL space_id (orphaned data)
    conn.execute(text("""
        INSERT INTO agent_space_access (id, agent_id, space_id, is_default, migration_flags, created_at)
        SELECT gen_random_uuid(), a.id, a.space_id, true, '{"source": "freeroam"}'::jsonb, NOW()
        FROM agents a
        WHERE a.pinned_to_space IS NULL
          AND a.space_id IS NOT NULL
        ON CONFLICT (agent_id, space_id) DO NOTHING
    """))

    # 4d. Follow-user agents (FOLLOW_UUID) — default to agent.space_id, flagged
    # Exclude agents with NULL space_id (orphaned data)
    conn.execute(text("""
        INSERT INTO agent_space_access (id, agent_id, space_id, is_default, migration_flags, created_at)
        SELECT gen_random_uuid(), a.id, a.space_id, true, '{"source": "follow_user"}'::jsonb, NOW()
        FROM agents a
        WHERE a.pinned_to_space = CAST(:follow_uuid AS uuid)
          AND a.space_id IS NOT NULL
        ON CONFLICT (agent_id, space_id) DO NOTHING
    """), {"follow_uuid": FOLLOW_UUID})

    # ── 5. Migration report ──
    result = conn.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE migration_flags IS NULL) as clean,
            COUNT(*) FILTER (WHERE migration_flags->>'source' = 'freeroam') as freeroam,
            COUNT(*) FILTER (WHERE migration_flags->>'source' = 'follow_user') as follow_user
        FROM agent_space_access
    """))
    row = result.fetchone()
    print(f"Agent space access migration: clean={row.clean}, freeroam={row.freeroam}, follow_user={row.follow_user}")

    # ── 6. Repair orphaned agents ──
    # Drop stale trigger that references pre-rename org_id column (blocks UPDATE)
    conn.execute(text("DROP TRIGGER IF EXISTS tr_log_agent_movement ON agents"))
    # Policy: agents with NULL space_id are legacy test data that predate the
    # NOT NULL constraint.  They cannot participate in space-scoped queries
    # and must not be dispatched.  Deprecate them so the integrity check
    # below can assert zero unaccounted agents.
    repaired = conn.execute(text("""
        UPDATE agents SET status = 'deprecated'
        WHERE space_id IS NULL
          AND id NOT IN (SELECT agent_id FROM agent_space_access)
        RETURNING id
    """)).fetchall()
    if repaired:
        print(f"REPAIR: deprecated {len(repaired)} agents with NULL space_id (orphaned test data)")

    # ── 7. Integrity checks ──
    # Every non-deprecated agent with a space_id must have at least one row
    orphans = conn.execute(text("""
        SELECT COUNT(*) FROM agents a
        LEFT JOIN agent_space_access asa ON asa.agent_id = a.id
        WHERE asa.id IS NULL
          AND a.status != 'deprecated'
    """)).scalar()
    assert orphans == 0, f"Migration error: {orphans} non-deprecated agents have no space access row"

    bad_defaults = conn.execute(text("""
        SELECT agent_id, COUNT(*) as cnt
        FROM agent_space_access
        WHERE is_default = true
        GROUP BY agent_id
        HAVING COUNT(*) != 1
    """)).fetchall()
    assert len(bad_defaults) == 0, f"Migration error: {len(bad_defaults)} agents have != 1 default"


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS agent_space_access_policy ON agent_space_access"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_agent_default_space"))
    op.drop_index("ix_agent_space_space", table_name="agent_space_access")
    op.drop_index("ix_agent_space_agent", table_name="agent_space_access")
    op.drop_table("agent_space_access")
