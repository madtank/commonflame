"""Create interactive context catalog tables

Revision ID: ctxcat_v1_20260525
Revises: oauth_as03_refresh_agent_binding
Create Date: 2026-05-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "ctxcat_v1_20260525"
down_revision: Union[str, None] = "oauth_as03_refresh_agent_binding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "context_catalog_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_type", sa.String(100), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("owner_id", sa.String(100), nullable=True),
        sa.Column("shelf", sa.String(80), nullable=True),
        sa.Column("current_context_object_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_state_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("thumbnail_ref", sa.Text(), nullable=True),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_context_catalog_entries_space_shelf", "context_catalog_entries", ["space_id", "shelf"])
    op.create_index("ix_context_catalog_entries_space_pinned", "context_catalog_entries", ["space_id", "pinned"])
    op.create_index(
        "ix_context_catalog_entries_space_created_at",
        "context_catalog_entries",
        ["space_id", "created_at"],
    )
    op.create_index(
        "ix_context_catalog_entries_current_context_object_id",
        "context_catalog_entries",
        ["current_context_object_id"],
    )
    op.create_index(
        "ix_context_catalog_entries_current_artifact_version_id",
        "context_catalog_entries",
        ["current_artifact_version_id"],
    )
    op.create_index(
        "ix_context_catalog_entries_current_state_version_id",
        "context_catalog_entries",
        ["current_state_version_id"],
    )

    op.create_table(
        "context_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("catalog_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False, server_default=sa.text("'interactive_context'")),
        sa.Column("artifact_kind", sa.String(80), nullable=False),
        sa.Column("action_set_id", sa.String(100), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["catalog_entry_id"], ["context_catalog_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_context_objects_catalog_entry_id", "context_objects", ["catalog_entry_id"])
    op.create_index("ix_context_objects_space_action_set", "context_objects", ["space_id", "action_set_id"])
    op.create_index("ix_context_objects_space_created_at", "context_objects", ["space_id", "created_at"])

    op.create_table(
        "context_artifact_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("catalog_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("content_ref", sa.Text(), nullable=True),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("sandbox_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["catalog_entry_id"], ["context_catalog_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["context_object_id"], ["context_objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("context_object_id", "sha256", name="uq_context_artifact_versions_object_sha256"),
    )
    op.create_index(
        "ix_context_artifact_versions_catalog_entry_id",
        "context_artifact_versions",
        ["catalog_entry_id"],
    )
    op.create_index(
        "ix_context_artifact_versions_context_object_id",
        "context_artifact_versions",
        ["context_object_id"],
    )
    op.create_index(
        "ix_context_artifact_versions_space_created_at",
        "context_artifact_versions",
        ["space_id", "created_at"],
    )

    op.create_table(
        "context_state_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("catalog_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["catalog_entry_id"], ["context_catalog_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["context_object_id"], ["context_objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("context_object_id", "version_number", name="uq_context_state_versions_object_version"),
    )
    op.create_index("ix_context_state_versions_catalog_entry_id", "context_state_versions", ["catalog_entry_id"])
    op.create_index("ix_context_state_versions_context_object_id", "context_state_versions", ["context_object_id"])
    op.create_index(
        "ix_context_state_versions_space_created_at",
        "context_state_versions",
        ["space_id", "created_at"],
    )

    op.create_table(
        "context_patches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("catalog_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_state_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patch_type", sa.String(80), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("created_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["catalog_entry_id"], ["context_catalog_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["context_object_id"], ["context_objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["base_artifact_version_id"], ["context_artifact_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["base_state_version_id"], ["context_state_versions.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_context_patches_catalog_entry_id", "context_patches", ["catalog_entry_id"])
    op.create_index("ix_context_patches_context_object_id", "context_patches", ["context_object_id"])
    op.create_index(
        "ix_context_patches_space_status_created_at",
        "context_patches",
        ["space_id", "status", "created_at"],
    )

    op.create_table(
        "context_audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("catalog_entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("context_object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_set_id", sa.String(100), nullable=False),
        sa.Column("action_id", sa.String(100), nullable=False),
        sa.Column("source_lane", sa.String(40), nullable=False),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.String(100), nullable=False),
        sa.Column("policy_decision", sa.String(40), nullable=False),
        sa.Column("base_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("base_state_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_artifact_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("result_state_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["catalog_entry_id"], ["context_catalog_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["context_object_id"], ["context_objects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["space_id"], ["spaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["base_artifact_version_id"], ["context_artifact_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["base_state_version_id"], ["context_state_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["result_artifact_version_id"], ["context_artifact_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["result_state_version_id"], ["context_state_versions.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "context_object_id",
            "actor_type",
            "actor_id",
            "action_id",
            "idempotency_key",
            name="uq_context_audit_events_actor_action_idempotency",
        ),
    )
    op.create_index("ix_context_audit_events_catalog_entry_id", "context_audit_events", ["catalog_entry_id"])
    op.create_index("ix_context_audit_events_context_object_id", "context_audit_events", ["context_object_id"])
    op.create_index("ix_context_audit_events_space_created_at", "context_audit_events", ["space_id", "created_at"])
    op.create_index("ix_context_audit_events_action", "context_audit_events", ["action_set_id", "action_id"])

    op.create_foreign_key(
        "fk_context_catalog_entries_current_context_object",
        "context_catalog_entries",
        "context_objects",
        ["current_context_object_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_context_catalog_entries_current_artifact_version",
        "context_catalog_entries",
        "context_artifact_versions",
        ["current_artifact_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_context_catalog_entries_current_state_version",
        "context_catalog_entries",
        "context_state_versions",
        ["current_state_version_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_context_catalog_entries_current_state_version",
        "context_catalog_entries",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_context_catalog_entries_current_artifact_version",
        "context_catalog_entries",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_context_catalog_entries_current_context_object",
        "context_catalog_entries",
        type_="foreignkey",
    )

    op.drop_index("ix_context_audit_events_action", table_name="context_audit_events")
    op.drop_index("ix_context_audit_events_space_created_at", table_name="context_audit_events")
    op.drop_index("ix_context_audit_events_context_object_id", table_name="context_audit_events")
    op.drop_index("ix_context_audit_events_catalog_entry_id", table_name="context_audit_events")
    op.drop_table("context_audit_events")

    op.drop_index("ix_context_patches_space_status_created_at", table_name="context_patches")
    op.drop_index("ix_context_patches_context_object_id", table_name="context_patches")
    op.drop_index("ix_context_patches_catalog_entry_id", table_name="context_patches")
    op.drop_table("context_patches")

    op.drop_index("ix_context_state_versions_space_created_at", table_name="context_state_versions")
    op.drop_index("ix_context_state_versions_context_object_id", table_name="context_state_versions")
    op.drop_index("ix_context_state_versions_catalog_entry_id", table_name="context_state_versions")
    op.drop_table("context_state_versions")

    op.drop_index("ix_context_artifact_versions_space_created_at", table_name="context_artifact_versions")
    op.drop_index("ix_context_artifact_versions_context_object_id", table_name="context_artifact_versions")
    op.drop_index("ix_context_artifact_versions_catalog_entry_id", table_name="context_artifact_versions")
    op.drop_table("context_artifact_versions")

    op.drop_index("ix_context_objects_space_created_at", table_name="context_objects")
    op.drop_index("ix_context_objects_space_action_set", table_name="context_objects")
    op.drop_index("ix_context_objects_catalog_entry_id", table_name="context_objects")
    op.drop_table("context_objects")

    op.drop_index("ix_context_catalog_entries_current_state_version_id", table_name="context_catalog_entries")
    op.drop_index("ix_context_catalog_entries_current_artifact_version_id", table_name="context_catalog_entries")
    op.drop_index("ix_context_catalog_entries_current_context_object_id", table_name="context_catalog_entries")
    op.drop_index("ix_context_catalog_entries_space_created_at", table_name="context_catalog_entries")
    op.drop_index("ix_context_catalog_entries_space_pinned", table_name="context_catalog_entries")
    op.drop_index("ix_context_catalog_entries_space_shelf", table_name="context_catalog_entries")
    op.drop_table("context_catalog_entries")
