"""create_message_read_status_table

Revision ID: mrs01_create_message_read_status
Revises: guest01, router03
Create Date: 2026-02-25 17:18:00

Root-fix migration for environments where message_read_status was never created
but later migrations/query paths expect it to exist.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "mrs01_create_message_read_status"
down_revision: Union[str, tuple[str, str], None] = ("guest01", "router03")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    try:
        return {idx.get("name") for idx in inspector.get_indexes(table_name) if idx.get("name")}
    except Exception:
        return set()


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    try:
        return {col.get("name") for col in inspector.get_columns(table_name) if col.get("name")}
    except Exception:
        return set()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    has_table = inspector.has_table("message_read_status")

    if not has_table:
        op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

        op.create_table(
            "message_read_status",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")),
            sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="CASCADE", name="fk_message_read_status_message"),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE", name="fk_message_read_status_agent"),
            sa.PrimaryKeyConstraint("id", name="pk_message_read_status"),
        )
    else:
        # Backfill minimal required columns for compatibility if a partial table exists.
        cols = _column_names(inspector, "message_read_status")

        if "read_at" not in cols:
            op.add_column("message_read_status", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))
        if "created_at" not in cols:
            op.add_column(
                "message_read_status",
                sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            )

    # Ensure canonical indexes expected by existing migration/query paths.
    inspector = sa.inspect(bind)
    index_names = _index_names(inspector, "message_read_status")

    if "idx_message_read_status_unique" not in index_names:
        op.create_index(
            "idx_message_read_status_unique",
            "message_read_status",
            ["message_id", "agent_id"],
            unique=True,
            postgresql_using="btree",
        )

    if "idx_message_read_status_agent_read" not in index_names:
        op.create_index(
            "idx_message_read_status_agent_read",
            "message_read_status",
            ["agent_id", "read_at"],
            postgresql_using="btree",
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_message_read_status_agent_read")
    op.execute("DROP INDEX IF EXISTS idx_message_read_status_unique")
    op.execute("DROP TABLE IF EXISTS message_read_status")
