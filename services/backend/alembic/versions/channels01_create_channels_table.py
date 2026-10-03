"""Create channels table and wire messages.channel_id FK

Revision ID: channels01
Revises: 02aa6bf237ed
Create Date: 2026-02-21

Owned by: @logic_runner_677
Signed off: @clawdbot_cipher (MCP architecture requirement)
Story: docs/guest-space-access-spec.md §6 (channel-level guest visibility)

Why a real channels table (not space_channel_settings varchar):
  - MCP messages tool needs: WHERE channel_id IN (SELECT id FROM channels WHERE guest_accessible)
  - FK chain: space_members → spaces → channels → messages (required for RLS policies)
  - Future per-channel features (muting, pinning, per-channel notifications) need a real entity
  - space_channel_settings was a placeholder — this replaces it

Migration strategy:
  Phase 1 (this migration):
    1. Create channels table
    2. Backfill from space_channel_settings (existing settings rows → proper channel rows)
    3. Discover any channels in messages not in space_channel_settings (guest_accessible=false by default)
    4. Add messages.channel_id (nullable UUID FK) — nullable for backward compat
    5. Backfill messages.channel_id via (space_id, name) join
    6. Keep messages.channel varchar — still populated for all existing code, dropped in Phase 2

  Phase 2 (future migration, separate):
    - Drop space_channel_settings (superseded by channels table)
    - Consider making messages.channel_id NOT NULL (after all writes populate it)
    - Consider dropping messages.channel varchar (after all reads migrate to channel_id)

  Why two phases: Phase 1 is safe to run at any time. Phase 2 requires verifying all
  application code has migrated to channel_id before dropping the varchar column.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = 'channels01'
down_revision: Union[str, None] = '02aa6bf237ed'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create channels table
    # ------------------------------------------------------------------
    op.create_table(
        'channels',
        sa.Column(
            'id',
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
            nullable=False,
        ),
        sa.Column(
            'space_id',
            UUID(as_uuid=True),
            sa.ForeignKey('organizations.id', ondelete='CASCADE'),
            nullable=False,
            comment='Space this channel belongs to (organizations.id)',
        ),
        sa.Column(
            'name',
            sa.String(50),
            nullable=False,
            comment='Channel name (matches messages.channel varchar)',
        ),
        sa.Column(
            'guest_accessible',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
            comment='When true, guests with active space_members row can read this channel',
        ),
        sa.Column(
            'created_at',
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text('NOW()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text('NOW()'),
            nullable=False,
        ),
    )
    # (space_id, name) uniqueness enforced — one row per channel per space
    op.create_unique_constraint('uq_channels_space_name', 'channels', ['space_id', 'name'])
    op.create_index('ix_channels_space_id', 'channels', ['space_id'])
    op.create_index('ix_channels_guest_accessible', 'channels', ['guest_accessible'])

    # ------------------------------------------------------------------
    # 2. Backfill from space_channel_settings (preserves existing settings)
    # ------------------------------------------------------------------
    op.execute("""
        INSERT INTO channels (space_id, name, guest_accessible, created_at, updated_at)
        SELECT
            scs.space_id,
            scs.channel_name,
            scs.guest_accessible,
            NOW(),
            scs.updated_at
        FROM space_channel_settings scs
        ON CONFLICT ON CONSTRAINT uq_channels_space_name DO UPDATE
            SET guest_accessible = EXCLUDED.guest_accessible,
                updated_at = EXCLUDED.updated_at
    """)

    # ------------------------------------------------------------------
    # 3. Discover channels in messages not covered by space_channel_settings
    #    (these are channels that exist in practice but have no explicit settings)
    #    Default: guest_accessible = false (deny by default — safe)
    # ------------------------------------------------------------------
    op.execute("""
        INSERT INTO channels (space_id, name, guest_accessible)
        SELECT DISTINCT m.org_id, m.channel, false
        FROM messages m
        WHERE m.channel IS NOT NULL
        ON CONFLICT ON CONSTRAINT uq_channels_space_name DO NOTHING
    """)

    # ------------------------------------------------------------------
    # 4. Add messages.channel_id (nullable FK → channels.id)
    #    Nullable: existing messages without a matched channel row are OK in Phase 1
    # ------------------------------------------------------------------
    op.add_column(
        'messages',
        sa.Column(
            'channel_id',
            UUID(as_uuid=True),
            sa.ForeignKey('channels.id', ondelete='SET NULL'),
            nullable=True,
            comment='FK to channels.id — populated by Phase 1 backfill. '
                    'Supersedes messages.channel varchar (Phase 2 will drop it).',
        ),
    )
    op.create_index('ix_messages_channel_id', 'messages', ['channel_id'])

    # ------------------------------------------------------------------
    # 5. Backfill messages.channel_id
    # ------------------------------------------------------------------
    op.execute("""
        UPDATE messages m
        SET channel_id = c.id
        FROM channels c
        WHERE c.space_id = m.org_id
          AND c.name = m.channel
          AND m.channel IS NOT NULL
    """)


def downgrade() -> None:
    # Phase 1 rollback: remove channel_id from messages, drop channels table
    # space_channel_settings is NOT touched — it still exists and was not modified
    op.drop_index('ix_messages_channel_id', table_name='messages')
    op.drop_column('messages', 'channel_id')
    op.drop_index('ix_channels_guest_accessible', table_name='channels')
    op.drop_index('ix_channels_space_id', table_name='channels')
    op.drop_constraint('uq_channels_space_name', 'channels', type_='unique')
    op.drop_table('channels')
