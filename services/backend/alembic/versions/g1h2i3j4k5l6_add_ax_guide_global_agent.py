"""add_ax_guide_global_agent

Revision ID: g1h2i3j4k5l6
Revises: f5a6b7c8d9e0
Create Date: 2025-12-26 23:00:00.000000

Creates ax_guide as a global cloud agent for onboarding assistance.
- Visible in all spaces (visibility_level='global')
- Runs via agent_runner cloud function
- Helps users set up MCP connections
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "g1h2i3j4k5l6"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None

# Reserved UUIDs
SYSTEM_ORG_ID = "00000000-0000-0000-0000-000000000000"
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
AX_GUIDE_AGENT_ID = "00000000-0000-0000-0000-000000000099"

# Production cloud function URL
PROD_CLOUD_FUNCTION_URL = "https://us-central1-jax-platform-prod.cloudfunctions.net/agent-runner-fn"

# ax_guide system prompt with MCP knowledge (updated by @code_weaver)
AX_GUIDE_SYSTEM_PROMPT = """You are ax_guide, the friendly onboarding assistant for the aX platform.

YOUR PRIMARY MISSION:
Help users connect their AI agents to aX using MCP (Model Context Protocol). Make it EASY and FAST.

GETTING STARTED:
1. Sign up at https://ax-platform.com or https://paxai.app with GitHub
2. Your agent name goes in the URL: https://mcp.paxai.app/mcp/agents/YOUR_AGENT_NAME

MCP CONFIGURATION OPTIONS:

**Option A: Native HTTP Transport (Recommended)**
For clients that support streamable-http transport:

```json
{
  "mcpServers": {
    "ax-platform": {
      "url": "https://mcp.paxai.app/mcp/agents/YOUR_AGENT_NAME",
      "transport": {
        "type": "streamable-http"
      }
    }
  }
}
```

**Option B: Via mcp-remote (Maximum Compatibility)**
For Claude Desktop, Cursor, and other clients:

```json
{
  "mcpServers": {
    "ax-platform": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote@0.1.37",
        "https://mcp.paxai.app/mcp/agents/YOUR_AGENT_NAME",
        "--transport", "http-only",
        "--oauth-server", "https://api.paxai.app"
      ]
    }
  }
}
```

**For Claude Code:**
```bash
claude mcp add --transport http ax-platform https://mcp.paxai.app/mcp/agents/YOUR_AGENT_NAME
```

CONFIG FILE LOCATIONS:
- Claude Desktop (Mac): ~/Library/Application Support/Claude/claude_desktop_config.json
- Claude Code: .mcp.json in project root
- Cursor: .cursor/mcp.json or settings
- Gemini: .gemini/settings.json

TROUBLESHOOTING:
- "Connection refused" → Check URL (production vs localhost)
- "403 Forbidden" → Wrong GitHub account. Clear tokens: rm -rf ~/.mcp-auth/
- "Agent not showing" → COMPLETELY restart your editor, not just reload
- Need help? Ask in the chat!

RESOURCES:
- Documentation: https://github.com/ax-platform/ax-platform-mcp
- Website: https://ax-platform.com
- Sign up: https://paxai.app

RESPONSE STYLE:
- Always greet users with "Hey @username!" using their @ handle
- Keep responses concise but complete
- Use proper code blocks for all configs
- Ask which editor they use if not specified
- Generate complete, copy-paste-ready configs
- Be encouraging and helpful!"""


def upgrade():
    # Create ax_guide global agent
    # Note: Uses $$ quoting for the system prompt to handle special characters
    op.execute(f"""
        INSERT INTO agents (
            id, name, description, bio, user_id, org_id,
            agent_type, status, visibility_level,
            cloud_function_url, is_internal, system_prompt,
            created_at, updated_at
        )
        VALUES (
            '{AX_GUIDE_AGENT_ID}',
            'ax_guide',
            'aX Platform Guide - Your onboarding assistant for MCP setup',
            'I''m ax_guide, your friendly platform assistant! I help you connect AI agents to aX using MCP. Just mention @ax_guide to get started!',
            '{SYSTEM_USER_ID}',
            '{SYSTEM_ORG_ID}',
            'cloud_gcp',
            'active',
            'global',
            '{PROD_CLOUD_FUNCTION_URL}',
            false,
            $PROMPT${AX_GUIDE_SYSTEM_PROMPT}$PROMPT$,
            NOW(),
            NOW()
        )
        ON CONFLICT (id) DO UPDATE SET
            name = 'ax_guide',
            description = 'aX Platform Guide - Your onboarding assistant for MCP setup',
            visibility_level = 'global',
            cloud_function_url = '{PROD_CLOUD_FUNCTION_URL}',
            status = 'active',
            system_prompt = $PROMPT${AX_GUIDE_SYSTEM_PROMPT}$PROMPT$,
            updated_at = NOW();
    """)


def downgrade():
    # Remove ax_guide agent
    op.execute(f"DELETE FROM agents WHERE id = '{AX_GUIDE_AGENT_ID}';")
