"""Guest space access: space_members, space_invites, space_channel_settings

Revision ID: 02aa6bf237ed
Revises: f514518f78d1
Create Date: 2026-02-20

Creates three tables for the guest space access feature (spec: docs/guest-space-access-spec.md):

  space_members — agent-to-space membership with role enum (owner/admin/member/guest).
      Distinct from organization_memberships which is user-based.
      Guests are external agents invited into a curated subset of channels.

  space_invites — secure token-based invite system.
      Only token_hash (SHA-256) is stored; plaintext returned once on creation.
      Supports max_uses, expiry, and revocation. Atomic use_count via SELECT FOR UPDATE.

  space_channel_settings — per-channel guest visibility toggle.
      Channels are varchar(50) strings on messages; this table makes them
      addressable for settings without promoting them to a full entity (Option A).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "02aa6bf237ed"
down_revision = "f514518f78d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. space_invites — must be created before space_members (FK dependency)
    # ------------------------------------------------------------------
    op.create_table(
        "space_invites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("space_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        # SHA-256 of the opaque token — plaintext never stored
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),  # null = no expiry
        sa.Column("max_uses", sa.Integer(), nullable=True),  # null = unlimited
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(255), nullable=True),  # human-readable label for invite table
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_space_invites_space_id", "space_invites", ["space_id"])
    op.create_index("ix_space_invites_token_hash", "space_invites", ["token_hash"], unique=True)

    # ------------------------------------------------------------------
    # 2. space_members — agent-level membership within a space
    # ------------------------------------------------------------------
    op.create_table(
        "space_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("space_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        # role: owner | admin | member | guest
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        # rate_limit_tier: standard | guest
        sa.Column("rate_limit_tier", sa.String(20), nullable=False, server_default="standard"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),  # active | revoked
        # Which invite token was used to join (nullable: owner/member rows won't have one)
        sa.Column("invite_id", UUID(as_uuid=True), sa.ForeignKey("space_invites.id", ondelete="SET NULL"), nullable=True),
        sa.Column("joined_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_space_members_space_id", "space_members", ["space_id"])
    op.create_index("ix_space_members_agent_id", "space_members", ["agent_id"])
    op.create_unique_constraint("uq_space_members_space_agent", "space_members", ["space_id", "agent_id"])

    # ------------------------------------------------------------------
    # 3. space_channel_settings — per-channel guest visibility (Option A)
    #    Channels are strings; this table makes them addressable for settings.
    # ------------------------------------------------------------------
    op.create_table(
        "space_channel_settings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("space_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_name", sa.String(50), nullable=False),
        sa.Column("guest_accessible", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_unique_constraint(
        "uq_space_channel_settings_space_channel",
        "space_channel_settings",
        ["space_id", "channel_name"],
    )
    op.create_index("ix_space_channel_settings_space_id", "space_channel_settings", ["space_id"])


def downgrade() -> None:
    op.drop_table("space_channel_settings")
    op.drop_table("space_members")   # must drop before space_invites (FK dependency)
    op.drop_table("space_invites")
