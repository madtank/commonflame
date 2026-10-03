"""Persist OAuth AS authorization, device, and refresh state

Revision ID: oauth_as01_persist_as_state
Revises: ta01_typed_task_assignee
Create Date: 2026-05-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "oauth_as01_persist_as_state"
down_revision: Union[str, None] = "ta01_typed_task_assignee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oauth_authorization_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("redirect_uri", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_challenge", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_oauth_authorization_codes_code_hash",
        "oauth_authorization_codes",
        ["code_hash"],
    )
    op.create_index(
        "ix_oauth_authorization_codes_client_id",
        "oauth_authorization_codes",
        ["client_id"],
    )
    op.create_index(
        "ix_oauth_authorization_codes_owner_user_id",
        "oauth_authorization_codes",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_oauth_authorization_codes_expires_at",
        "oauth_authorization_codes",
        ["expires_at"],
    )

    op.create_table(
        "oauth_device_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("device_code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("user_code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("resource", sa.Text(), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_oauth_device_codes_device_code_hash",
        "oauth_device_codes",
        ["device_code_hash"],
    )
    op.create_index("ix_oauth_device_codes_user_code_hash", "oauth_device_codes", ["user_code_hash"])
    op.create_index("ix_oauth_device_codes_client_id", "oauth_device_codes", ["client_id"])
    op.create_index("ix_oauth_device_codes_owner_user_id", "oauth_device_codes", ["owner_user_id"])
    op.create_index("ix_oauth_device_codes_status", "oauth_device_codes", ["status"])
    op.create_index("ix_oauth_device_codes_expires_at", "oauth_device_codes", ["expires_at"])

    op.create_table(
        "oauth_refresh_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("refresh_token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_from_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_oauth_refresh_tokens_refresh_token_hash",
        "oauth_refresh_tokens",
        ["refresh_token_hash"],
    )
    op.create_index("ix_oauth_refresh_tokens_client_id", "oauth_refresh_tokens", ["client_id"])
    op.create_index("ix_oauth_refresh_tokens_owner_user_id", "oauth_refresh_tokens", ["owner_user_id"])
    op.create_index("ix_oauth_refresh_tokens_expires_at", "oauth_refresh_tokens", ["expires_at"])
    op.create_index(
        "ix_oauth_refresh_tokens_active",
        "oauth_refresh_tokens",
        ["client_id", "owner_user_id", "revoked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_oauth_refresh_tokens_active", table_name="oauth_refresh_tokens")
    op.drop_index("ix_oauth_refresh_tokens_expires_at", table_name="oauth_refresh_tokens")
    op.drop_index("ix_oauth_refresh_tokens_owner_user_id", table_name="oauth_refresh_tokens")
    op.drop_index("ix_oauth_refresh_tokens_client_id", table_name="oauth_refresh_tokens")
    op.drop_index("ix_oauth_refresh_tokens_refresh_token_hash", table_name="oauth_refresh_tokens")
    op.drop_table("oauth_refresh_tokens")

    op.drop_index("ix_oauth_device_codes_expires_at", table_name="oauth_device_codes")
    op.drop_index("ix_oauth_device_codes_status", table_name="oauth_device_codes")
    op.drop_index("ix_oauth_device_codes_owner_user_id", table_name="oauth_device_codes")
    op.drop_index("ix_oauth_device_codes_client_id", table_name="oauth_device_codes")
    op.drop_index("ix_oauth_device_codes_user_code_hash", table_name="oauth_device_codes")
    op.drop_index("ix_oauth_device_codes_device_code_hash", table_name="oauth_device_codes")
    op.drop_table("oauth_device_codes")

    op.drop_index("ix_oauth_authorization_codes_expires_at", table_name="oauth_authorization_codes")
    op.drop_index(
        "ix_oauth_authorization_codes_owner_user_id",
        table_name="oauth_authorization_codes",
    )
    op.drop_index("ix_oauth_authorization_codes_client_id", table_name="oauth_authorization_codes")
    op.drop_index("ix_oauth_authorization_codes_code_hash", table_name="oauth_authorization_codes")
    op.drop_table("oauth_authorization_codes")
