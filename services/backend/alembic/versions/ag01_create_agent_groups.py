"""Create agent_groups + agent_group_members tables.

Revision ID: ag01_agent_groups
Revises: 8e5c38a8d310
Create Date: 2026-06-03

First-class, space-scoped agent groups. A group is a named collection of
agents that a user OR an agent can create; sending to a group expands to
individual mentioned_agent_ids at send time (see messages_service).

Both tables carry space_id and use the standard space_isolation RLS pattern
(space_id = app.current_space_id, or privileged). Members denormalize
space_id so the policy needs no join.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "ag01_agent_groups"
down_revision: Union[str, tuple[str, ...], None] = "8e5c38a8d310"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANT = """
    DO $$
    DECLARE
        target_role text;
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'axdev_app') THEN
            target_role := 'axdev_app';
        ELSE
            target_role := current_user;
        END IF;
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO %I', '{table}', target_role);
    END $$;
"""

_SPACE = "space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid"
_PRIV = "current_setting('app.is_privileged', true) = 'true'"


def _enable_space_rls(table: str, policy: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(_GRANT.format(table=table)))
    op.execute(sa.text(f"""
        CREATE POLICY {policy} ON {table}
        FOR ALL
        USING ({_PRIV} OR {_SPACE})
        WITH CHECK ({_PRIV} OR {_SPACE})
    """))


def upgrade() -> None:
    # ── agent_groups ──
    op.create_table(
        "agent_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_type", sa.String(length=10), nullable=False, server_default="user"),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("owner_agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("visibility", sa.String(length=20), nullable=False, server_default="space"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_dynamic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("dynamic_rules", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint("owner_type IN ('user', 'agent', 'space')", name="ck_agent_group_owner_type"),
        sa.CheckConstraint("visibility IN ('space', 'private')", name="ck_agent_group_visibility"),
    )
    op.create_unique_constraint("uq_agent_group_space_name", "agent_groups", ["space_id", "name"])
    op.create_index("ix_agent_group_space", "agent_groups", ["space_id"])
    op.create_index("ix_agent_group_owner_user", "agent_groups", ["owner_user_id"])
    op.create_index("ix_agent_group_owner_agent", "agent_groups", ["owner_agent_id"])
    _enable_space_rls("agent_groups", "agent_groups_space_isolation")

    # ── agent_group_members ──
    op.create_table(
        "agent_group_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agent_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("added_by_type", sa.String(length=10), nullable=True),
        sa.Column("added_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("added_by_agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.CheckConstraint(
            "added_by_type IS NULL OR added_by_type IN ('user', 'agent', 'system')",
            name="ck_agent_group_member_added_by_type",
        ),
    )
    op.create_unique_constraint("uq_agent_group_member", "agent_group_members", ["group_id", "agent_id"])
    op.create_index("ix_agent_group_member_group", "agent_group_members", ["group_id"])
    op.create_index("ix_agent_group_member_agent", "agent_group_members", ["agent_id"])
    op.create_index("ix_agent_group_member_space", "agent_group_members", ["space_id"])
    _enable_space_rls("agent_group_members", "agent_group_members_space_isolation")


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS agent_group_members_space_isolation ON agent_group_members"))
    op.drop_index("ix_agent_group_member_space", table_name="agent_group_members")
    op.drop_index("ix_agent_group_member_agent", table_name="agent_group_members")
    op.drop_index("ix_agent_group_member_group", table_name="agent_group_members")
    op.drop_table("agent_group_members")

    op.execute(sa.text("DROP POLICY IF EXISTS agent_groups_space_isolation ON agent_groups"))
    op.drop_index("ix_agent_group_owner_agent", table_name="agent_groups")
    op.drop_index("ix_agent_group_owner_user", table_name="agent_groups")
    op.drop_index("ix_agent_group_space", table_name="agent_groups")
    op.drop_table("agent_groups")
