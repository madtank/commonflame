"""Create agent_keys table for OAuth client_credentials (SEP-1046)

Revision ID: ak01_agent_keys
Revises: z9y8x7w6v5u4
Create Date: 2026-02-15

Adds the agent_keys table to support headless agent authentication
via the OAuth 2.1 client_credentials grant. Each row represents a
Confidential Client identity for an agent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'ak01_agent_keys'
down_revision: Union[str, None] = 'fix1m2c3p4o5r6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_keys',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('client_id', sa.String(64), unique=True, nullable=False),
        sa.Column('client_secret_hash', sa.String(128), nullable=False),
        sa.Column('key_prefix', sa.String(12), nullable=False),
        sa.Column('scopes', sa.String(255), nullable=False, server_default='mcp:read mcp:write'),
        sa.Column('label', sa.String(255), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_agent_keys_agent_id', 'agent_keys', ['agent_id'])
    op.create_index('ix_agent_keys_client_id', 'agent_keys', ['client_id'], unique=True)
    op.create_index('ix_agent_keys_agent_id_revoked', 'agent_keys', ['agent_id', 'revoked_at'])


def downgrade() -> None:
    op.drop_index('ix_agent_keys_agent_id_revoked', table_name='agent_keys')
    op.drop_index('ix_agent_keys_client_id', table_name='agent_keys')
    op.drop_index('ix_agent_keys_agent_id', table_name='agent_keys')
    op.drop_table('agent_keys')
