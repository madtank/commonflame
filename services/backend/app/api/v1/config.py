"""
Configuration endpoints for frontend consumption.

Provides version maps, feature flags, and other runtime configuration
that the frontend needs without hardcoding values.
"""

from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from app.core.config import settings

router = APIRouter(prefix="/config", tags=["config"])


def _validate_url(url: str, name: str) -> None:
    """
    Validate URL format. Raises HTTPException if invalid.

    Uses 503 Service Unavailable for configuration errors since they represent
    a temporary deployment issue that can be resolved by fixing environment variables.
    """
    if not url:
        return  # Empty URLs are allowed (v2 may not be configured)
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"Missing scheme or netloc in {name}")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Invalid {name} configuration: {str(e)}"
        )


class AgentVersionInfo(BaseModel):
    """Version URL mapping for agent runners."""
    version: str
    url: str
    description: str


class AgentVersionsResponse(BaseModel):
    """Response containing all available agent runner versions."""
    versions: dict[str, AgentVersionInfo]
    default_version: str
    current_v1_url: str
    current_v2_url: str


@router.get("/agent-versions", response_model=AgentVersionsResponse)
async def get_agent_versions():
    """
    Get available agent runner versions and their URLs.

    Used by frontend to:
    - Infer current version from cloud_function_url
    - Get correct URL when toggling versions
    - Determine if an agent has a "custom" URL (not in this map)
    """
    v1_url = settings.agent_runner_v1_url
    v2_url = settings.agent_runner_v2_url

    # Validate v1 URL is configured (required)
    if not v1_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent runner v1 URL not configured"
        )

    # Validate URL formats
    _validate_url(v1_url, "AGENT_RUNNER_V1_URL")
    _validate_url(v2_url, "AGENT_RUNNER_V2_URL")

    versions = {
        "v1": AgentVersionInfo(
            version="v1",
            url=v1_url,
            description="Stable agent runner"
        ),
    }

    # Only include v2 if configured
    if v2_url:
        versions["v2"] = AgentVersionInfo(
            version="v2",
            url=v2_url,
            description="Next-gen agent runner with image generation and MCPToolFactory"
        )

    return AgentVersionsResponse(
        versions=versions,
        default_version="v1",
        current_v1_url=v1_url,
        current_v2_url=v2_url or ""
    )
