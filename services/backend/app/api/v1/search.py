"""
Search & Analytics API endpoints
Handles message search and trending topics analysis
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, text, cast, String
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import uuid
import logging

logger = logging.getLogger(__name__)

from ...core.rls import SecureSession, get_secure_session
from ...models.user import User
from ...models.message import Message
from ...services.message_visibility import exclude_ui_only_no_reply_clause
from ...models.agent import Agent

router = APIRouter(prefix="/api", tags=["search"])

# Pydantic models for API
class MessageSearchQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="Search query")
    limit: int = Field(50, le=200, description="Number of results to return")
    offset: int = Field(0, ge=0, description="Number of results to skip")
    channel: Optional[str] = Field(None, description="Filter by channel")
    sender_type: Optional[str] = Field(None, pattern="^(user|agent)$", description="Filter by sender type")
    date_from: Optional[datetime] = Field(None, description="Filter messages from this date")
    date_to: Optional[datetime] = Field(None, description="Filter messages until this date")

class MessageSearchResult(BaseModel):
    id: str
    content: str
    summary: Optional[str] = None
    sender: str
    sender_type: str  # "user" or "agent"
    channel: str
    timestamp: datetime
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    relevance_score: float  # Simple relevance scoring
    context: str  # Snippet with highlighted matches

class MessageSearchResponse(BaseModel):
    results: List[MessageSearchResult]
    total: int
    limit: int
    offset: int
    query: str
    execution_time_ms: float

class TrendingTopic(BaseModel):
    topic: str
    count: int
    trend_score: float
    sample_messages: List[str]
    channels: List[str]
    time_period: str

class TrendingTopicsResponse(BaseModel):
    topics: List[TrendingTopic]
    analysis_period: str
    total_messages_analyzed: int
    generated_at: datetime


def create_context_snippet(content: str, query: str, max_length: int = 150) -> str:
    """Create a context snippet with highlighted search terms"""
    query_words = query.lower().split()
    content_lower = content.lower()

    # Find the best position to center the snippet
    best_pos = 0
    max_matches = 0

    for i in range(len(content) - max_length + 1):
        snippet = content_lower[i:i + max_length]
        matches = sum(1 for word in query_words if word in snippet)
        if matches > max_matches:
            max_matches = matches
            best_pos = i

    # Extract snippet and add ellipsis if needed
    start = max(0, best_pos)
    end = min(len(content), start + max_length)
    snippet = content[start:end]

    if start > 0:
        snippet = "..." + snippet
    if end < len(content):
        snippet = snippet + "..."

    return snippet


@router.post("/search/messages")
async def search_messages(
    search_query: MessageSearchQuery,
    session: SecureSession = Depends(get_secure_session)
):
    """Search messages with full-text search using GIN indexes and optional filters."""
    start_time = datetime.utcnow()

    try:
        # Sanitize query for ILIKE fallback (escape SQL wildcards)
        safe_query = search_query.query.replace("%", "\\%").replace("_", "\\_")

        # Build full-text search using to_tsvector/plainto_tsquery (uses GIN index)
        ts_query = func.plainto_tsquery("english", search_query.query)
        ts_vector = func.to_tsvector("english", Message.content)
        ts_rank = func.ts_rank(ts_vector, ts_query)

        # Full-text match OR ILIKE fallback for partial/non-English matches
        # OR UUID prefix match (supports short IDs like "862caee3")
        fts_condition = or_(
            ts_vector.op("@@")(ts_query),
            Message.content.ilike(f"%{safe_query}%"),
            cast(Message.id, String).ilike(f"{safe_query}%"),
        )

        # Base query with org isolation
        query = (
            select(Message, ts_rank.label("rank"))
            .options(selectinload(Message.user), selectinload(Message.agent))
            .where(Message.space_id == session.space_id)
            .where(exclude_ui_only_no_reply_clause())
            .where(fts_condition)
        )

        # Apply optional filters
        if search_query.channel:
            query = query.where(Message.channel == search_query.channel)

        if search_query.sender_type:
            if search_query.sender_type == "user":
                query = query.where(Message.user_id.isnot(None))
            elif search_query.sender_type == "agent":
                query = query.where(Message.agent_id.isnot(None))

        if search_query.date_from:
            query = query.where(Message.created_at >= search_query.date_from)

        if search_query.date_to:
            query = query.where(Message.created_at <= search_query.date_to)

        # Efficient count using func.count() instead of fetching all IDs
        count_query = select(func.count()).select_from(
            query.with_only_columns(Message.id).subquery()
        )
        total = (await session.db.execute(count_query)).scalar() or 0

        # Order by relevance rank (desc), then recency; apply pagination
        query = (
            query.order_by(text("rank DESC"), Message.created_at.desc())
            .offset(search_query.offset)
            .limit(search_query.limit)
        )

        rows = (await session.db.execute(query)).all()

        # Build response
        search_results = []
        for row in rows:
            message = row[0]  # Message object
            rank = row[1]     # ts_rank score

            # Determine sender info
            if message.agent:
                sender = message.agent.name
                sender_type = "agent"
                agent_id = str(message.agent_id)
                user_id = None
            elif message.user:
                sender = message.user.username
                sender_type = "user"
                agent_id = None
                user_id = str(message.user_id)
            else:
                sender = "Unknown"
                sender_type = "unknown"
                agent_id = None
                user_id = None

            search_results.append(MessageSearchResult(
                id=str(message.id),
                content=message.content,
                summary=message.ai_summary,
                sender=sender,
                sender_type=sender_type,
                channel=message.channel or "general",
                timestamp=message.created_at,
                agent_id=agent_id,
                user_id=user_id,
                relevance_score=float(rank),
                context=create_context_snippet(message.content, search_query.query),
            ))

        execution_time = (datetime.utcnow() - start_time).total_seconds() * 1000

        logger.info(
            f"Search '{search_query.query}' returned {len(search_results)}/{total} results in {execution_time:.0f}ms"
        )

        # Return format expected by frontend
        return {
            "success": True,
            "messages": search_results,
            "topics": [],
        }

    except Exception as e:
        logger.error(f"Search failed for org={session.space_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search failed. Please try again.",
        )


@router.get("/search/trending-topics", response_model=TrendingTopicsResponse)
async def get_trending_topics(
    hours: int = Query(24, ge=1, le=168, description="Time period in hours to analyze"),
    limit: int = Query(10, ge=1, le=50, description="Number of topics to return"),
    min_mentions: int = Query(3, ge=2, le=20, description="Minimum mentions for a topic to be trending"),
    session: SecureSession = Depends(get_secure_session)
):
    """Get trending topics based on message content analysis"""
    try:
        # Calculate time threshold
        time_threshold = datetime.utcnow() - timedelta(hours=hours)

        # Get messages from the specified time period
        result = await session.db.execute(
            select(Message).options(selectinload(Message.user), selectinload(Message.agent))
            .where(and_(
                Message.space_id == session.space_id,
                Message.created_at >= time_threshold
            ))
            .order_by(Message.created_at.desc())
        )
        messages = result.scalars().all()

        if not messages:
            return TrendingTopicsResponse(
                topics=[],
                analysis_period=f"{hours} hours",
                total_messages_analyzed=0,
                generated_at=datetime.utcnow()
            )

        # Simple topic extraction (word frequency analysis)
        word_counts = {}
        word_to_messages = {}
        channel_counts = {}

        # Common words to filter out
        stop_words = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by',
            'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did',
            'will', 'would', 'could', 'should', 'may', 'might', 'can', 'this', 'that', 'these', 'those',
            'i', 'you', 'he', 'she', 'it', 'we', 'they', 'me', 'him', 'her', 'us', 'them', 'my', 'your',
            'his', 'her', 'its', 'our', 'their', 'am', 'pm', 'just', 'now', 'here', 'there', 'when',
            'where', 'why', 'how', 'what', 'who', 'which', 'all', 'any', 'some', 'no', 'not', 'very',
            'too', 'so', 'more', 'most', 'much', 'many', 'few', 'little', 'less', 'get', 'got', 'go',
            'going', 'come', 'coming', 'see', 'look', 'know', 'think', 'say', 'said', 'tell', 'make',
            'take', 'give', 'work', 'want', 'need', 'like', 'use', 'good', 'new', 'first', 'last',
            'long', 'great', 'little', 'own', 'other', 'old', 'right', 'big', 'high', 'different',
            'small', 'large', 'next', 'early', 'young', 'important', 'few', 'public', 'bad', 'same',
            'able'
        }

        for message in messages:
            # Extract words (simple tokenization)
            words = message.content.lower().split()
            channel = message.channel or "general"

            for word in words:
                # Clean word (remove punctuation)
                word = ''.join(c for c in word if c.isalnum())

                # Filter out short words, numbers, and stop words
                if len(word) >= 3 and not word.isdigit() and word not in stop_words:
                    word_counts[word] = word_counts.get(word, 0) + 1

                    if word not in word_to_messages:
                        word_to_messages[word] = []
                    word_to_messages[word].append(message.content[:100] + "..." if len(message.content) > 100 else message.content)

                    if word not in channel_counts:
                        channel_counts[word] = set()
                    channel_counts[word].add(channel)

        # Calculate trending topics
        trending_topics = []
        total_words = sum(word_counts.values())

        for word, count in word_counts.items():
            if count >= min_mentions:
                # Simple trend score calculation
                # Higher frequency + recency + channel diversity = higher trend score
                frequency_score = count / total_words if total_words > 0 else 0
                channel_diversity = len(channel_counts[word])
                trend_score = frequency_score * 100 + channel_diversity * 10

                trending_topics.append(TrendingTopic(
                    topic=word,
                    count=count,
                    trend_score=trend_score,
                    sample_messages=word_to_messages[word][:3],  # First 3 sample messages
                    channels=list(channel_counts[word]),
                    time_period=f"{hours} hours"
                ))

        # Sort by trend score and limit results
        trending_topics.sort(key=lambda x: x.trend_score, reverse=True)
        trending_topics = trending_topics[:limit]

        return TrendingTopicsResponse(
            topics=trending_topics,
            analysis_period=f"{hours} hours",
            total_messages_analyzed=len(messages),
            generated_at=datetime.utcnow()
        )

    except Exception as e:
        logger.error(f"Trending topics failed for org={session.space_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to analyze trending topics. Please try again.",
        )
