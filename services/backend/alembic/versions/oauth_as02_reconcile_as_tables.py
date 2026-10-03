"""Reconcile OAuth AS tables after stamped production state.

Revision ID: oauth_as02_reconcile_as_tables
Revises: oauth_as01_persist_as_state
Create Date: 2026-05-24

Production can be stamped at the OAuth AS head while still missing legacy
DCR storage from the older oauth_clients branch. This forward migration makes
the AS storage physical again without rewinding Alembic state.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "oauth_as02_reconcile_as_tables"
down_revision: Union[str, None] = "oauth_as01_persist_as_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_clients (
            client_id VARCHAR(255) PRIMARY KEY,
            client_secret VARCHAR(255),
            client_name VARCHAR(255) NOT NULL,
            redirect_uris TEXT[] NOT NULL,
            grant_types TEXT[] NOT NULL DEFAULT ARRAY[
                'authorization_code',
                'refresh_token'
            ]::TEXT[],
            response_types TEXT[] NOT NULL DEFAULT ARRAY['code']::TEXT[],
            token_endpoint_auth_method VARCHAR(50) NOT NULL DEFAULT 'none',
            scope TEXT,
            registration_access_token VARCHAR(255) UNIQUE,
            registration_client_uri VARCHAR(500),
            metadata JSONB DEFAULT '{}'::jsonb,
            is_trusted BOOLEAN NOT NULL DEFAULT false,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_oauth_clients_name ON oauth_clients (client_name)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_oauth_clients_created_at
        ON oauth_clients (created_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_oauth_clients_is_active
        ON oauth_clients (is_active)
    """)

    op.execute("""
        INSERT INTO oauth_clients (
            client_id,
            client_name,
            redirect_uris,
            grant_types,
            response_types,
            token_endpoint_auth_method,
            is_trusted,
            is_active,
            metadata
        ) VALUES
        (
            'http-native-mcp-client',
            'HTTP Native MCP Client',
            ARRAY['urn:ietf:wg:oauth:2.0:oob', 'http://localhost:*']::TEXT[],
            ARRAY['authorization_code', 'refresh_token']::TEXT[],
            ARRAY['code']::TEXT[],
            'none',
            true,
            true,
            '{"description": "Pre-registered client for v1-http-native OAuth flow"}'::jsonb
        ),
        (
            'mcpjam-inspector-local',
            'MCPJam Inspector (Local)',
            ARRAY[
                'http://127.0.0.1:6274/oauth/callback',
                'http://127.0.0.1:6274/callback',
                'http://127.0.0.1:6274/oauth/callback/debug',
                'http://localhost:6274/oauth/callback',
                'http://localhost:6274/callback'
            ]::TEXT[],
            ARRAY['authorization_code', 'refresh_token']::TEXT[],
            ARRAY['code']::TEXT[],
            'none',
            true,
            true,
            jsonb_build_object(
                'description',
                'MCPJam Inspector for local MCP testing',
                'homepage_uri',
                'https://www.mcpjam.com'
            )
        ),
        (
            'mcp-remote-cli',
            'MCP CLI Proxy (mcp-remote)',
            ARRAY['http://localhost:*', 'http://127.0.0.1:*']::TEXT[],
            ARRAY['authorization_code', 'refresh_token']::TEXT[],
            ARRAY['code']::TEXT[],
            'none',
            true,
            true,
            '{"description": "mcp-remote CLI proxy for AI agent MCP access"}'::jsonb
        ),
        (
            'claude-code-mcp',
            'Claude Code MCP',
            ARRAY['http://localhost:*', 'http://127.0.0.1:*']::TEXT[],
            ARRAY['authorization_code', 'refresh_token']::TEXT[],
            ARRAY['code']::TEXT[],
            'none',
            true,
            true,
            '{"description": "Claude Code built-in MCP client"}'::jsonb
        )
        ON CONFLICT (client_id) DO NOTHING
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code_hash VARCHAR(64) NOT NULL UNIQUE,
            client_id VARCHAR(255) NOT NULL,
            redirect_uri TEXT NOT NULL,
            scope TEXT NOT NULL,
            resource TEXT NOT NULL,
            owner_user_id UUID NOT NULL,
            code_challenge TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_authorization_codes_code_hash
        ON oauth_authorization_codes (code_hash)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_authorization_codes_client_id
        ON oauth_authorization_codes (client_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_authorization_codes_owner_user_id
        ON oauth_authorization_codes (owner_user_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_authorization_codes_expires_at
        ON oauth_authorization_codes (expires_at)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_device_codes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            device_code_hash VARCHAR(64) NOT NULL UNIQUE,
            user_code_hash VARCHAR(64) NOT NULL UNIQUE,
            client_id VARCHAR(255) NOT NULL,
            scope TEXT,
            resource TEXT,
            owner_user_id UUID,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            expires_at TIMESTAMPTZ NOT NULL,
            approved_at TIMESTAMPTZ,
            consumed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_device_codes_device_code_hash
        ON oauth_device_codes (device_code_hash)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_device_codes_user_code_hash
        ON oauth_device_codes (user_code_hash)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_device_codes_client_id
        ON oauth_device_codes (client_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_device_codes_owner_user_id
        ON oauth_device_codes (owner_user_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_device_codes_status
        ON oauth_device_codes (status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_device_codes_expires_at
        ON oauth_device_codes (expires_at)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            refresh_token_hash VARCHAR(64) NOT NULL UNIQUE,
            client_id VARCHAR(255) NOT NULL,
            owner_user_id UUID NOT NULL,
            scope TEXT NOT NULL,
            resource TEXT NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            rotated_from_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_refresh_tokens_refresh_token_hash
        ON oauth_refresh_tokens (refresh_token_hash)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_refresh_tokens_client_id
        ON oauth_refresh_tokens (client_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_refresh_tokens_owner_user_id
        ON oauth_refresh_tokens (owner_user_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_refresh_tokens_expires_at
        ON oauth_refresh_tokens (expires_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_oauth_refresh_tokens_active
        ON oauth_refresh_tokens (client_id, owner_user_id, revoked_at)
    """)


def downgrade() -> None:
    # This migration reconciles missing production tables that may have existed
    # through older migration paths, so downgrade intentionally leaves data in place.
    pass
