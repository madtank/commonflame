"""Built-in onboarding capabilities and fixed OAuth approval workspace."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "ws02_builtin_onboarding"
down_revision = "td01_lock_task_number_allocation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "account_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("spaces.id")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_account_invites_token_hash", "account_invites", ["token_hash"], unique=True)
    for table in ("oauth_authorization_codes", "oauth_device_codes", "oauth_refresh_tokens"):
        op.add_column(table, sa.Column("authorized_space_id", postgresql.UUID(as_uuid=True)))


def downgrade():
    for table in ("oauth_refresh_tokens", "oauth_device_codes", "oauth_authorization_codes"):
        op.drop_column(table, "authorized_space_id")
    op.drop_table("account_invites")
