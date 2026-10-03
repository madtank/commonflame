"""BE-08: add unique constraint on (org_id, name) for agents table

Revision ID: f514518f78d1
Revises: z9y8x7w6v5u4
Create Date: 2026-02-20

Deduplicates existing agent rows where (org_id, name) collides, then adds
UNIQUE(org_id, name). For each duplicate group we keep the row with the most
FK references (messages authored) — i.e. the "active" agent — and rename the
stale copies to `<name>_dup_<short_id>` so they remain queryable but no longer
block the constraint.  Rename is used instead of hard-delete so the migration
is safely reversible and data is not silently lost.

Root cause: AUTO_REGISTER_AGENTS=true could fire twice in quick succession;
both requests passed the collision check before either committed, producing two
rows.  The unique constraint + idempotent INSERT in oauth_shim.py closes this.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers
revision = "f514518f78d1"
down_revision = "ff01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # -------------------------------------------------------------------------
    # Step 1: Find all (org_id, name) groups with more than one agent row
    # -------------------------------------------------------------------------
    duplicates = conn.execute(text("""
        SELECT org_id, name, COUNT(*) AS cnt
        FROM agents
        GROUP BY org_id, name
        HAVING COUNT(*) > 1
        ORDER BY org_id, name
    """)).fetchall()

    if duplicates:
        print(f"\n[BE-08] Found {len(duplicates)} duplicate (org_id, name) group(s) — resolving...")

        for org_id, name, cnt in duplicates:
            # For each group, order by message count DESC so the most-referenced
            # agent sorts first; fall back to oldest created_at as tiebreaker.
            rows = conn.execute(text("""
                SELECT a.id,
                       COALESCE(msg.msg_count, 0) AS msg_count,
                       a.created_at
                FROM agents a
                LEFT JOIN (
                    SELECT agent_id, COUNT(*) AS msg_count
                    FROM messages
                    WHERE agent_id IS NOT NULL
                    GROUP BY agent_id
                ) msg ON msg.agent_id = a.id
                WHERE a.org_id = :org_id AND a.name = :name
                ORDER BY msg_count DESC, a.created_at ASC
            """), {"org_id": str(org_id), "name": name}).fetchall()

            # Keep the first row (most messages, oldest created_at tiebreak)
            canonical_id = rows[0][0]
            stale_rows = rows[1:]

            for stale_id, msg_count, created_at in stale_rows:
                short_id = str(stale_id)[:8]
                new_name = f"{name}_dup_{short_id}"

                # Reassign any messages from stale → canonical so no data orphaned
                if msg_count > 0:
                    conn.execute(text("""
                        UPDATE messages
                        SET agent_id = :canonical
                        WHERE agent_id = :stale
                    """), {"canonical": str(canonical_id), "stale": str(stale_id)})

                # Rename stale agent (not deleted — recoverable)
                conn.execute(text("""
                    UPDATE agents
                    SET name = :new_name
                    WHERE id = :stale_id
                """), {"new_name": new_name, "stale_id": str(stale_id)})

                print(f"  [BE-08] Renamed stale '{name}' ({stale_id}) → '{new_name}' "
                      f"(msgs reassigned: {msg_count})")

        print("[BE-08] Deduplication complete.")

    # -------------------------------------------------------------------------
    # Step 2: Add the unique constraint
    # -------------------------------------------------------------------------
    op.create_index(
        "uq_agents_org_name",
        "agents",
        ["org_id", "name"],
        unique=True,
    )
    print("[BE-08] UNIQUE(org_id, name) constraint added.")


def downgrade() -> None:
    op.drop_index("uq_agents_org_name", table_name="agents")
