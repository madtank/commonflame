"""
Agent Templates API - Read-only endpoints for template discovery.

Provides the template gallery data for frontend consumption.
No authentication required - templates are public metadata.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.database import get_db_session
from ...core.rls import AdminSession, get_admin_session

router = APIRouter(prefix="/api/agent-templates", tags=["agent-templates"])

# Shared column list for template queries
TEMPLATE_COLUMNS = """
    key, name, badge, icon_url, parent_key,
    short_description, full_description, default_bio,
    system_prompt_additions, available_models, default_tools,
    template_source, is_top_level, display_order, is_active
"""


class AgentTemplateResponse(BaseModel):
    """Single agent template."""
    key: str
    name: str
    badge: Optional[str] = None
    icon_url: Optional[str] = None
    parent_key: Optional[str] = None
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    default_bio: Optional[str] = None
    system_prompt_additions: Optional[str] = None
    available_models: Optional[list[str]] = None
    default_tools: Optional[dict] = None
    template_source: str
    is_top_level: bool
    display_order: int
    is_active: bool


class AvailableToolResponse(BaseModel):
    """MCP server/tool metadata for dynamic UI."""
    id: str
    name: str
    description: Optional[str] = None
    category: str
    icon_url: Optional[str] = None
    min_user_tier: str
    required_runner_tag: Optional[str] = None


class AgentTemplatesListResponse(BaseModel):
    """Response containing all active templates and available tools."""
    templates: list[AgentTemplateResponse]
    available_tools: list[AvailableToolResponse]
    top_level_count: int
    total_count: int


def _row_to_template(row: Row[Any]) -> AgentTemplateResponse:
    """Convert a database row to an AgentTemplateResponse."""
    return AgentTemplateResponse(
        key=row.key,
        name=row.name,
        badge=row.badge,
        icon_url=row.icon_url,
        parent_key=row.parent_key,
        short_description=row.short_description,
        full_description=row.full_description,
        default_bio=row.default_bio,
        system_prompt_additions=row.system_prompt_additions,
        available_models=row.available_models,
        default_tools=row.default_tools,
        template_source=row.template_source,
        is_top_level=row.is_top_level,
        display_order=row.display_order,
        is_active=row.is_active,
    )


@router.get("", response_model=AgentTemplatesListResponse)
async def list_agent_templates(
    db: AsyncSession = Depends(get_db_session),
    include_inactive: bool = False,
):
    """
    Get all agent templates for the template gallery.

    Returns a flat list sorted by display_order within hierarchy:
    - Top-level templates first (is_top_level=true)
    - Then variants grouped under their parent

    Frontend uses this to render the template selection UI.
    """
    query = text(f"""
        SELECT {TEMPLATE_COLUMNS}
        FROM agent_templates
        WHERE (:include_inactive OR is_active = true)
          AND is_admin_only = false
        ORDER BY
            is_top_level DESC,
            CASE WHEN is_top_level THEN display_order ELSE 999 END,
            COALESCE(parent_key, key),
            display_order
    """)

    result = await db.execute(query, {"include_inactive": include_inactive})
    rows = result.fetchall()

    templates = [_row_to_template(row) for row in rows]
    top_level_count = sum(1 for t in templates if t.is_top_level)

    tools_query = text("""
        SELECT key, name, description, category, min_user_tier, required_runner_tag
        FROM mcp_servers
        WHERE is_active = true
        ORDER BY category, name
    """)
    tools_result = await db.execute(tools_query)
    tools_rows = tools_result.fetchall()

    available_tools = [
        AvailableToolResponse(
            id=row.key,
            name=row.name,
            description=row.description,
            category=row.category,
            icon_url=None,
            min_user_tier=row.min_user_tier,
            required_runner_tag=row.required_runner_tag,
        )
        for row in tools_rows
    ]

    return AgentTemplatesListResponse(
        templates=templates,
        available_tools=available_tools,
        top_level_count=top_level_count,
        total_count=len(templates),
    )


@router.get("/{template_key}", response_model=AgentTemplateResponse)
async def get_agent_template(
    template_key: str,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Get a single template by key.

    Used when user selects a template to see full details.
    """
    query = text(f"""
        SELECT {TEMPLATE_COLUMNS}
        FROM agent_templates
        WHERE key = :key
          AND is_active = true
          AND is_admin_only = false
    """)

    result = await db.execute(query, {"key": template_key})
    row = result.fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_key}' not found",
        )

    return _row_to_template(row)
