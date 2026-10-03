"""create credential_fingerprints table

Revision ID: cf01_create_credential_fingerprints
Revises: bac01_bound_agent_cred
Create Date: 2026-03-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "cf01_create_credential_fingerprints"
down_revision = "bac01_bound_agent_cred"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "credential_fingerprints",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("host_binding", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_credential_fingerprints_agent_id", "credential_fingerprints", ["agent_id"])
    op.create_index("ix_credential_fingerprints_token_sha256", "credential_fingerprints", ["token_sha256"])


def downgrade():
    op.drop_index("ix_credential_fingerprints_token_sha256", table_name="credential_fingerprints")
    op.drop_index("ix_credential_fingerprints_agent_id", table_name="credential_fingerprints")
    op.drop_table("credential_fingerprints")
