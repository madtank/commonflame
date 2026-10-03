"""Create credentials table for unified principal auth

Revision ID: cred01_create_credentials
Revises: rls01_reenable_rls
Create Date: 2026-03-12

Unified credentials table supporting user PATs and (future) system account
secrets. RLS policy with privileged bypass for credential resolution.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "cred01_create_credentials"
down_revision = "rls01_reenable_rls"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "credentials",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("space_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("principal_type", sa.String(20), nullable=False),
        sa.Column("principal_id", UUID(as_uuid=True), nullable=False),
        sa.Column("credential_type", sa.String(20), nullable=False),
        sa.Column("key_id", sa.String(16), nullable=False, unique=True),
        sa.Column("secret_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("scopes", sa.JSON(), server_default='["api:read","api:write"]'),
        sa.Column("allowed_agent_ids", sa.JSON(), nullable=True),
        sa.Column("created_by_principal_type", sa.String(20), nullable=True),
        sa.Column("created_by_principal_id", UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
    )

    op.create_index("ix_credentials_key_id", "credentials", ["key_id"])
    op.create_index("ix_credentials_principal", "credentials", ["principal_type", "principal_id"])
    op.create_index("ix_credentials_space", "credentials", ["space_id"])

    # RLS with privileged bypass
    op.execute(sa.text("ALTER TABLE credentials ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("""
        CREATE POLICY credentials_space_isolation ON credentials
        FOR ALL USING (
            current_setting('app.is_privileged', true) = 'true'
            OR space_id::text = NULLIF(current_setting('app.current_space_id', true), '')
        )
        WITH CHECK (
            current_setting('app.is_privileged', true) = 'true'
            OR space_id::text = NULLIF(current_setting('app.current_space_id', true), '')
        )
    """))


def downgrade():
    op.execute(sa.text("DROP POLICY IF EXISTS credentials_space_isolation ON credentials"))
    op.execute(sa.text("ALTER TABLE credentials DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_credentials_space")
    op.drop_index("ix_credentials_principal")
    op.drop_index("ix_credentials_key_id")
    op.drop_table("credentials")
