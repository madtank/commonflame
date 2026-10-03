"""create oauth_clients table for Dynamic Client Registration (RFC 7591)

Revision ID: 020_oauth_clients
Revises: 019_case_insensitive_agent_name
Create Date: 2025-11-15 00:00:00.000000

Changes:
1. Create oauth_clients table for storing dynamically registered OAuth clients
2. Support RFC 7591 Dynamic Client Registration protocol
3. Enable universal MCP client access without whitelisting

Background:
- Replaces hardcoded TRUSTED_MCP_CLIENTS whitelist approach
- Any standards-compliant MCP client can register via /oauth/register
- Clients register their metadata (name, redirect URIs, scopes) dynamically
- Server issues unique client_id (and optional client_secret)
- Authorization flow validates against registered clients in DB

This enables:
- Universal client access (MCPJam, Claude, any future MCP client)
- Proper OAuth 2.1 compliance
- Scalable client management without manual whitelist updates
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY, TEXT
import uuid

# revision identifiers, used by Alembic.
revision = '020_oauth_clients'
down_revision = '019_case_insensitive_agent_name'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create oauth_clients table for Dynamic Client Registration.

    Implements RFC 7591 client metadata storage.
    """
    op.create_table(
        'oauth_clients',
        # Primary identifier
        sa.Column('client_id', sa.String(255), primary_key=True, nullable=False,
                  comment='OAuth 2.0 client identifier (unique per registered client)'),

        # Client credentials (optional for public clients)
        sa.Column('client_secret', sa.String(255), nullable=True,
                  comment='Hashed client secret (null for public clients using PKCE)'),

        # Client metadata (RFC 7591)
        sa.Column('client_name', sa.String(255), nullable=False,
                  comment='Human-readable client name (e.g., "MCPJam Inspector")'),
        sa.Column('redirect_uris', ARRAY(TEXT), nullable=False,
                  comment='Array of allowed redirect URIs for this client'),
        sa.Column('grant_types', ARRAY(TEXT), nullable=False, default=['authorization_code', 'refresh_token'],
                  comment='Allowed OAuth grant types'),
        sa.Column('response_types', ARRAY(TEXT), nullable=False, default=['code'],
                  comment='Allowed OAuth response types'),
        sa.Column('token_endpoint_auth_method', sa.String(50), nullable=False, default='none',
                  comment='Client authentication method (none=public/PKCE, client_secret_post, etc.)'),

        # Scopes and capabilities
        sa.Column('scope', TEXT, nullable=True,
                  comment='Space-separated list of requested scopes'),

        # Registration metadata
        sa.Column('registration_access_token', sa.String(255), nullable=True, unique=True,
                  comment='Token for updating this client registration (RFC 7592)'),
        sa.Column('registration_client_uri', sa.String(500), nullable=True,
                  comment='URI for managing this client registration (RFC 7592)'),

        # Additional metadata
        sa.Column('metadata', JSONB, nullable=True, default={},
                  comment='Additional client metadata (logo_uri, contacts, etc.)'),

        # Trust and policy
        sa.Column('is_trusted', sa.Boolean, nullable=False, default=False,
                  comment='Whether this client is trusted (can skip consent)'),
        sa.Column('is_active', sa.Boolean, nullable=False, default=True,
                  comment='Whether this client is active (can be revoked)'),

        # Audit timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'),
                  onupdate=sa.text('NOW()'), nullable=False),

        # Indexes
        sa.Index('idx_oauth_clients_name', 'client_name'),
        sa.Index('idx_oauth_clients_created_at', 'created_at'),
        sa.Index('idx_oauth_clients_is_active', 'is_active'),
    )

    # Seed well-known MCP clients (optional, for backwards compatibility)
    # These are pre-registered to avoid breaking existing integrations
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
        -- HTTP Native Client (v1-http-native flow)
        (
            'http-native-mcp-client',
            'HTTP Native MCP Client',
            ARRAY['urn:ietf:wg:oauth:2.0:oob', 'http://localhost:*'],
            ARRAY['authorization_code', 'refresh_token'],
            ARRAY['code'],
            'none',
            true,
            true,
            '{"description": "Pre-registered client for v1-http-native OAuth flow"}'::jsonb
        ),
        -- MCPJam Inspector (localhost testing)
        (
            'mcpjam-inspector-local',
            'MCPJam Inspector (Local)',
            ARRAY['http://127.0.0.1:6274/oauth/callback', 'http://127.0.0.1:6274/callback', 'http://127.0.0.1:6274/oauth/callback/debug', 'http://localhost:6274/oauth/callback', 'http://localhost:6274/callback'],
            ARRAY['authorization_code', 'refresh_token'],
            ARRAY['code'],
            'none',
            true,
            true,
            '{"description": "MCPJam Inspector for local MCP testing", "homepage_uri": "https://www.mcpjam.com"}'::jsonb
        ),
        -- mcp-remote CLI Proxy
        (
            'mcp-remote-cli',
            'MCP CLI Proxy (mcp-remote)',
            ARRAY['http://localhost:*', 'http://127.0.0.1:*'],
            ARRAY['authorization_code', 'refresh_token'],
            ARRAY['code'],
            'none',
            true,
            true,
            '{"description": "mcp-remote CLI proxy for AI agent MCP access"}'::jsonb
        ),
        -- Claude Code MCP
        (
            'claude-code-mcp',
            'Claude Code MCP',
            ARRAY['http://localhost:*', 'http://127.0.0.1:*'],
            ARRAY['authorization_code', 'refresh_token'],
            ARRAY['code'],
            'none',
            true,
            true,
            '{"description": "Claude Code built-in MCP client"}'::jsonb
        )
        ON CONFLICT (client_id) DO NOTHING;
    """)


def downgrade() -> None:
    """
    Drop oauth_clients table.

    WARNING: This will remove all dynamically registered clients!
    Existing OAuth tokens will still work, but new client registrations will fail.
    """
    op.drop_table('oauth_clients')
