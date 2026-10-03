"""Create conversation_cards table

Revision ID: cc01_conv_cards
Revises: ax02_deactivate
Create Date: 2026-03-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "cc01_conv_cards"
down_revision = "origins02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "conversation_cards",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("root_message_id", UUID(as_uuid=True), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("channel", sa.String(50), nullable=False, server_default="main"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("previous_summary", sa.Text(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("participants", JSONB, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_conv_cards_org_activity", "conversation_cards", ["org_id", "last_activity_at"], postgresql_using="btree")
    op.create_index("ix_conv_cards_root_msg", "conversation_cards", ["root_message_id"], unique=True)


def downgrade():
    op.drop_index("ix_conv_cards_root_msg")
    op.drop_index("ix_conv_cards_org_activity")
    op.drop_table("conversation_cards")
