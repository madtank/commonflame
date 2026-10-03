"""
Admin AI Outreach API Endpoints

SECURITY LEVEL: MAXIMUM (Super Admin Only)
Requires: super_admin role + BETA_MODE=true

AI-powered outreach features with strict guardrails and human-in-the-loop approval.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from typing import Dict, List, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, validator
from datetime import datetime
import logging

from ...core.database import get_db_session
from ...core.rls import AdminSession, get_admin_session
from ...core.super_admin_security import require_super_admin, SuperAdminAuditLog
from ...services.ai_outreach_service import get_ai_outreach_service, AIOutreachGuardrails
from ...models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin/ai-outreach", tags=["admin-ai-outreach"])


class AIOutreachRequest(BaseModel):
    """Request model for AI outreach generation"""
    user_id: str = Field(..., description="Target user ID")
    campaign: str = Field(..., description="Campaign type")
    tone: str = Field(default="friendly", description="Tone of message")
    context: Optional[str] = Field(None, description="Additional context")

    @validator('campaign')
    def validate_campaign(cls, v):
        valid_campaigns = ['activation', 'feature_launch', 're_engagement', 'power_user']
        if v not in valid_campaigns:
            raise ValueError(f"Campaign must be one of: {valid_campaigns}")
        return v

    @validator('tone')
    def validate_tone(cls, v):
        valid_tones = ['friendly', 'professional', 'enthusiastic', 'casual']
        if v not in valid_tones:
            raise ValueError(f"Tone must be one of: {valid_tones}")
        return v


class AIBatchOutreachRequest(BaseModel):
    """Request model for batch AI outreach"""
    user_ids: List[str] = Field(..., description="List of target user IDs", max_items=10)
    campaign: str = Field(..., description="Campaign type")
    tone: str = Field(default="friendly", description="Tone of message")
    context: Optional[str] = Field(None, description="Additional context")

    @validator('user_ids')
    def validate_user_ids(cls, v):
        if len(v) > AIOutreachGuardrails.MAX_BATCH_SIZE:
            raise ValueError(f"Batch size cannot exceed {AIOutreachGuardrails.MAX_BATCH_SIZE}")
        return v

    @validator('campaign')
    def validate_campaign(cls, v):
        valid_campaigns = ['activation', 'feature_launch', 're_engagement', 'power_user']
        if v not in valid_campaigns:
            raise ValueError(f"Campaign must be one of: {valid_campaigns}")
        return v


@router.get("/status")
async def get_ai_outreach_status(
    super_admin: User = Depends(require_super_admin)
) -> Dict[str, Any]:
    """
    Get AI outreach service status

    Returns:
        Service availability, configuration, and limits
    """

    service = get_ai_outreach_service()

    return {
        "ai_available": service.gemini_available,
        "beta_mode": True,  # If this endpoint is reachable, BETA_MODE is true
        "super_admin": super_admin.username,
        "guardrails": {
            "max_batch_size": AIOutreachGuardrails.MAX_BATCH_SIZE,
            "max_content_length": AIOutreachGuardrails.MAX_LENGTH,
            "blocked_patterns_count": len(AIOutreachGuardrails.BLOCKED_PATTERNS),
            "disclaimer_required": True
        },
        "campaigns_available": [
            "activation",
            "feature_launch",
            "re_engagement",
            "power_user"
        ],
        "tones_available": [
            "friendly",
            "professional",
            "enthusiastic",
            "casual"
        ]
    }


@router.post("/generate")
async def generate_ai_outreach(
    request_data: AIOutreachRequest,
    admin_session: AdminSession = Depends(get_admin_session),
    super_admin: User = Depends(require_super_admin),
    request: Request = None
) -> Dict[str, Any]:
    """
    Generate AI-powered outreach content for a single user

    Security:
    - Requires super_admin role
    - Requires BETA_MODE=true
    - Content is validated by guardrails
    - Audit logged
    - Human approval required before sending

    Returns:
        Generated content with validation status and metadata
    """

    try:
        # Get target user data
        from sqlalchemy import select
        result = await admin_session.db.execute(
            select(User).where(User.id == request_data.user_id)
        )
        target_user = result.scalar_one_or_none()

        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Target user not found"
            )

        # Build user data for AI
        user_data = {
            "id": str(target_user.id),
            "username": target_user.username,
            "email": target_user.email,
            "agent_count": 0,  # TODO: Fetch from agents table
            "message_count": 0,  # TODO: Fetch from messages table
            "task_count": 0,  # TODO: Fetch from tasks table
            "days_since_creation": (datetime.now() - target_user.created_at.replace(tzinfo=None)).days,
            "activity_status": "active"  # TODO: Calculate from activity
        }

        # Generate AI content
        service = get_ai_outreach_service()
        result = await service.generate_outreach_email(
            user_data=user_data,
            campaign=request_data.campaign,
            tone=request_data.tone,
            context=request_data.context
        )

        # Audit log
        SuperAdminAuditLog.log_access(
            user=super_admin,
            endpoint="/api/admin/ai-outreach/generate",
            action=f"Generated AI outreach for user {target_user.username} (campaign: {request_data.campaign})",
            request=request,
            success=result.get("success", False)
        )

        # Add target user info to result
        result["target_user"] = {
            "id": str(target_user.id),
            "username": target_user.username,
            "email": target_user.email
        }

        result["warning"] = (
            "⚠️ AI-generated content requires human review before sending. "
            "Review for accuracy, tone, and appropriateness."
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating AI outreach: {e}", exc_info=True)
        SuperAdminAuditLog.log_access(
            user=super_admin,
            endpoint="/api/admin/ai-outreach/generate",
            action=f"Generate AI outreach failed",
            request=request,
            success=False,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate AI outreach: {str(e)}"
        )


@router.post("/generate-batch")
async def generate_batch_ai_outreach(
    request_data: AIBatchOutreachRequest,
    admin_session: AdminSession = Depends(get_admin_session),
    super_admin: User = Depends(require_super_admin),
    request: Request = None
) -> Dict[str, Any]:
    """
    Generate AI-powered outreach content for multiple users

    Security:
    - Requires super_admin role
    - Requires BETA_MODE=true
    - Limited to MAX_BATCH_SIZE users
    - All content validated by guardrails
    - Audit logged

    Returns:
        Batch results with individual validation status
    """

    try:
        # Validate batch size
        if len(request_data.user_ids) > AIOutreachGuardrails.MAX_BATCH_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Batch size exceeds maximum ({AIOutreachGuardrails.MAX_BATCH_SIZE})"
            )

        # Fetch all users
        from sqlalchemy import select
        result = await admin_session.db.execute(
            select(User).where(User.id.in_(request_data.user_ids))
        )
        target_users = result.scalars().all()

        if len(target_users) != len(request_data.user_ids):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="One or more target users not found"
            )

        # Build user data list
        users_data = []
        for user in target_users:
            users_data.append({
                "id": str(user.id),
                "username": user.username,
                "email": user.email,
                "agent_count": 0,
                "message_count": 0,
                "task_count": 0,
                "days_since_creation": (datetime.now() - user.created_at.replace(tzinfo=None)).days,
                "activity_status": "active"
            })

        # Generate batch AI content
        service = get_ai_outreach_service()
        result = await service.generate_batch_outreach(
            users=users_data,
            campaign=request_data.campaign,
            tone=request_data.tone,
            context=request_data.context
        )

        # Audit log
        SuperAdminAuditLog.log_access(
            user=super_admin,
            endpoint="/api/admin/ai-outreach/generate-batch",
            action=f"Generated batch AI outreach for {len(target_users)} users (campaign: {request_data.campaign})",
            request=request,
            success=result.get("success", False)
        )

        result["warning"] = (
            "⚠️ All AI-generated content requires human review before sending. "
            "Review each message for accuracy, tone, and appropriateness."
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating batch AI outreach: {e}", exc_info=True)
        SuperAdminAuditLog.log_access(
            user=super_admin,
            endpoint="/api/admin/ai-outreach/generate-batch",
            action="Generate batch AI outreach failed",
            request=request,
            success=False,
            error=str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate batch AI outreach: {str(e)}"
        )


@router.post("/validate")
async def validate_ai_content(
    content: str,
    super_admin: User = Depends(require_super_admin)
) -> Dict[str, Any]:
    """
    Validate AI-generated content against guardrails

    Useful for manual review and editing of AI-generated content
    """

    is_valid, error = AIOutreachGuardrails.validate_content(content)

    return {
        "valid": is_valid,
        "error": error,
        "length": len(content),
        "word_count": len(content.split()),
        "has_disclaimer": AIOutreachGuardrails.REQUIRED_DISCLAIMER.strip() in content,
        "validation_rules": {
            "max_length": AIOutreachGuardrails.MAX_LENGTH,
            "blocked_patterns": len(AIOutreachGuardrails.BLOCKED_PATTERNS),
            "disclaimer_required": True
        }
    }


@router.get("/audit-log")
async def get_ai_audit_log(
    super_admin: User = Depends(require_super_admin),
    limit: int = 50
) -> Dict[str, Any]:
    """
    Get audit log of all AI outreach operations

    Returns:
        List of AI generation events with metadata
    """

    service = get_ai_outreach_service()
    audit_log = service.get_audit_log()

    # Return most recent entries
    recent_entries = audit_log[-limit:] if len(audit_log) > limit else audit_log
    recent_entries.reverse()  # Most recent first

    return {
        "total_operations": len(audit_log),
        "showing": len(recent_entries),
        "entries": recent_entries,
        "super_admin": super_admin.username
    }


@router.get("/templates")
async def get_outreach_templates(
    super_admin: User = Depends(require_super_admin)
) -> Dict[str, Any]:
    """
    Get available fallback templates

    These are used when AI service is unavailable
    """

    return {
        "templates": {
            "activation": {
                "name": "User Activation",
                "description": "For users who signed up but haven't engaged",
                "suitable_for": ["new users", "low activity users"]
            },
            "feature_launch": {
                "name": "Feature Launch Announcement",
                "description": "Announce new features to existing users",
                "suitable_for": ["active users", "power users"]
            },
            "re_engagement": {
                "name": "Re-engagement",
                "description": "Win back dormant users",
                "suitable_for": ["dormant users", "churned users"]
            },
            "power_user": {
                "name": "Power User Recognition",
                "description": "Thank highly engaged users and request feedback",
                "suitable_for": ["power users", "advocates"]
            }
        },
        "campaigns": ["activation", "feature_launch", "re_engagement", "power_user"],
        "tones": ["friendly", "professional", "enthusiastic", "casual"]
    }
