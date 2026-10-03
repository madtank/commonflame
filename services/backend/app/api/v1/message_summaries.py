"""
AI Summary Generation for Messages
POST /api/v1/messages/{id}/summarize - Canonical generate/cache summary endpoint
POST /api/messages/{id}/summarize - Legacy compatibility alias

Uses AWS Bedrock Nova Micro for fast, cheap summarization.
This endpoint is Bedrock-only.
"""

import asyncio
import json as json_module
import logging
import os
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update, exists
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...core.database import get_db_session
from ...core.rls import SecureSession, get_secure_session
from ...models.message import Message
from ...models.user import User
from ...core.jwt_verify import get_current_user_from_token
from ...services.message_visibility import exclude_ui_only_no_reply_clause, is_ui_only_no_reply_metadata

router = APIRouter(prefix="/api", tags=["summaries"])
logger = logging.getLogger(__name__)

# Configure Bedrock
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-east-1")
BEDROCK_SUMMARY_MODEL = os.getenv(
    "BEDROCK_MESSAGE_SUMMARY_MODEL",
    os.getenv("BEDROCK_SUMMARY_MODEL", "us.amazon.nova-micro-v1:0"),
)

_bedrock_client = None


def _get_bedrock_client():
    """Lazy-init Bedrock runtime client."""
    global _bedrock_client
    if os.getenv("ENABLE_CLOUD_AI", "false").lower() != "true":
        return None
    if _bedrock_client is None:
        try:
            import boto3
            _bedrock_client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
            logger.info(f"Summary Bedrock client configured: region={BEDROCK_REGION}, model={BEDROCK_SUMMARY_MODEL}")
        except Exception as e:
            logger.error(f"Failed to initialize Bedrock client for summaries: {e}")
    return _bedrock_client


class SummarizeRequest(BaseModel):
    """Request body for summary generation (currently empty, reserved for future options)"""

    pass

class SummarizeResponse(BaseModel):
    """Response with generated or cached summary"""

    summary: str
    cached: bool
    message_id: str

async def _generate_summary_with_bedrock(
    content: str, context_messages: list[str], author_name: str | None = None
) -> str:
    """Generate summary using AWS Bedrock Nova Micro (async via run_in_executor)."""
    client = _get_bedrock_client()
    if not client:
        raise HTTPException(status_code=503, detail="AI service unavailable - Bedrock client not initialized")

    # Validate content
    if not content or not content.strip():
        raise HTTPException(status_code=400, detail="Message content is empty")
    if len(content) > 100000:
        raise HTTPException(status_code=400, detail="Message too long to summarize")

    # Build prompt with context
    context_text = "\n".join(f"- {msg[:200]}" for msg in context_messages[-10:])
    author_info = f"Author: {author_name}\n" if author_name else ""

    prompt = f"""You are summarizing a message from aX Platform - a multi-user, multi-agent
collaboration network where humans and AI agents work together in shared spaces.

{author_info}Previous messages for context:
{context_text}

Message to summarize:
{content}

Summarize in 2-3 concise sentences (40-75 words). Capture the key points. Be direct.
Start with what the author is discussing, not who they're addressing.
Output only the summary text."""

    body = json_module.dumps({
        "messages": [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
        "inferenceConfig": {
            "maxTokens": 256,
            "temperature": 0.1,
        },
    })

    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.invoke_model(
                modelId=BEDROCK_SUMMARY_MODEL,
                contentType="application/json",
                accept="application/json",
                body=body,
            ),
        )

        response_body = json_module.loads(response["body"].read())
        output_text = response_body["output"]["message"]["content"][0]["text"]

        if not output_text:
            raise HTTPException(status_code=500, detail="AI service returned empty response")

        return output_text.strip()

    except HTTPException:
        raise
    except Exception as e:
        logger.warning("Bedrock summary API error: %s", e)
        raise


async def _summarize_message_impl(
    message_id: str,
    session: SecureSession = Depends(get_secure_session)
):
    """
    Generate AI summary for a message (one-time only, cached in database)

    - Checks if summary already exists → returns cached
    - If not, calls Bedrock Nova Micro with message content + last 10 messages
    - Saves summary to database
    - Rate limited to 10 summaries/min per user (TODO: implement rate limiting)
    """
    # Parse message ID
    try:
        msg_uuid = UUID(message_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid message ID format")

    # Get the message with relationships eagerly loaded
    result = await session.db.execute(
        select(Message)
        .options(selectinload(Message.user))
        .options(selectinload(Message.agent))
        .where(Message.id == msg_uuid)
    )
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    if is_ui_only_no_reply_metadata(message.message_type, getattr(message, "message_metadata", None)):
        raise HTTPException(status_code=409, detail="UI-only no-reply signals are not eligible for summaries")

    # Authorization: Verify user is a member of the message's space
    # (Don't rely on current_space_id — it may be stale after space switching)
    from app.models.space_membership import SpaceMembership
    is_member = await session.db.scalar(
        select(exists().where(
            SpaceMembership.user_id == session.user.id,
            SpaceMembership.space_id == message.space_id,
        ))
    )
    if not is_member:
        raise HTTPException(
            status_code=403,
            detail=f"Not a member of space {message.space_id}. Switch spaces or check membership.",
        )

    # Check if summary already exists (cached)
    if message.ai_summary:
        return SummarizeResponse(summary=message.ai_summary, cached=True, message_id=str(message.id))

    # Get last 10 messages for context (from same org/channel)
    space_id = str(message.space_id)
    context_result = await session.db.execute(
        select(Message.content)
        .where(Message.space_id == space_id)
        .where(Message.channel == message.channel)
        .where(Message.created_at < message.created_at)
        .where(exclude_ui_only_no_reply_clause())
        .order_by(Message.created_at.desc())
        .limit(10)
    )
    context_messages = [row[0] for row in context_result.fetchall()]

    # Get author name (from user or agent)
    author_name = None
    if message.user:
        author_name = message.user.username or f"User {message.user_id}"
    elif message.agent:
        author_name = message.agent.name or f"Agent {message.agent_id}"

    # Generate summary (Bedrock only)
    try:
        summary = await _generate_summary_with_bedrock(
            content=message.content, context_messages=context_messages, author_name=author_name
        )
    except Exception as e:
        logger.error("Bedrock summary generation failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Bedrock summary generation failed",
        ) from e

    # Save to database with race condition protection (only update if still null)
    result = await session.db.execute(
        update(Message)
        .where(Message.id == msg_uuid)
        .where(Message.ai_summary.is_(None))  # Optimistic locking - only update if still null
        .values(ai_summary=summary)
    )
    await session.db.commit()

    # Check if our update succeeded (if rowcount is 0, someone else already generated it)
    if result.rowcount == 0:
        # Another request already generated the summary, fetch it
        await session.db.refresh(message)
        summary = message.ai_summary
        logger.info(f"AI summary for message {message_id} was generated concurrently by another request")
        return SummarizeResponse(summary=summary, cached=True, message_id=str(message.id))

    logger.info(f"Generated AI summary for message {message_id} by user {session.user.id}")

    return SummarizeResponse(summary=summary, cached=False, message_id=str(message.id))


@router.post("/v1/messages/{message_id}/summarize", response_model=SummarizeResponse)
async def summarize_message_v1(
    message_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    return await _summarize_message_impl(message_id=message_id, session=session)


@router.post("/messages/{message_id}/summarize", response_model=SummarizeResponse)
async def summarize_message_legacy(
    message_id: str,
    session: SecureSession = Depends(get_secure_session),
):
    return await _summarize_message_impl(message_id=message_id, session=session)
