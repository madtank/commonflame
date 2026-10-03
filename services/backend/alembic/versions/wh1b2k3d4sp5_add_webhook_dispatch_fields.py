"""add webhook dispatch fields for external agents

Revision ID: wh1b2k3d4sp5
Revises: y9z0a1b2c3d4
Create Date: 2026-01-28

Universal Webhook Dispatch: Enable external agents (Moltbot, Ollama, custom)
to receive HTTP POST notifications and use MCP tools for reasoning.

New fields:
- webhook_url: POST target for external agents
- webhook_secret: HMAC-SHA256 signing secret
- webhook_verified: Challenge-response verification status
- origin: Explicit agent classification (cloud | mcp | external_gateway)
- last_dispatch_error: Why dispatch failed (for debugging)
- fidelity_ms: Round-trip latency tracking
"""
from alembic import op
import sqlalchemy as sa


revision = 'wh1b2k3d4sp5'
down_revision = 'y9z0a1b2c3d4'
branch_labels = None
depends_on = None


def upgrade():
    # Add webhook dispatch columns
    op.add_column('agents', sa.Column('webhook_url', sa.String(512), nullable=True))
    op.add_column('agents', sa.Column('webhook_secret', sa.String(128), nullable=True))
    op.add_column('agents', sa.Column('webhook_verified', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('agents', sa.Column('origin', sa.String(20), nullable=False, server_default='cloud'))
    op.add_column('agents', sa.Column('last_dispatch_error', sa.Text(), nullable=True))
    op.add_column('agents', sa.Column('fidelity_ms', sa.Integer(), nullable=True))

    # Backfill origin based on existing data:
    # - Agents with cloud_function_url → origin = 'cloud'
    # - Agents without cloud_function_url → origin = 'mcp'
    op.execute("""
        UPDATE agents
        SET origin = CASE
            WHEN cloud_function_url IS NOT NULL THEN 'cloud'
            ELSE 'mcp'
        END
    """)

    # Create index for external gateway lookup
    op.create_index(
        'idx_agents_origin_webhook',
        'agents',
        ['origin', 'webhook_verified'],
        postgresql_where=sa.text("origin = 'external_gateway'")
    )


def downgrade():
    op.drop_index('idx_agents_origin_webhook', 'agents')
    op.drop_column('agents', 'fidelity_ms')
    op.drop_column('agents', 'last_dispatch_error')
    op.drop_column('agents', 'origin')
    op.drop_column('agents', 'webhook_verified')
    op.drop_column('agents', 'webhook_secret')
    op.drop_column('agents', 'webhook_url')
