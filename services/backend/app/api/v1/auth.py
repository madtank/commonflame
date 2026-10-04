"""Commonflame identity, scoped credential exchange, and authenticated messages."""
import logging
import os
import re
import uuid
from datetime import datetime, timedelta

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ...core.actor import CAP_MESSAGES_SEND, Actor
from ...core.beta_config import get_beta_config
from ...core.config import get_settings
from ...core.database import get_db_session
from ...core.jwt_verify import (
    get_current_user_from_token,
)
from ...core.rls import SecureSession, get_secure_session, SystemSession, get_system_session
from ...models.message import Message
from ...models.user import User

from ...services.messages_service import MessagesService
from ...services.redis_sse_broker import redis_sse_broker

# Redis client for unread tracking (mirror /api/messages)
redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6380/0"), decode_responses=True)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["authentication"])
# Historical PAT exchange is retained as source only; Commonflame never mounts it.
legacy_router = APIRouter(prefix="/auth", tags=["legacy-pat"])


# Pydantic models
class PlatformFeatures(BaseModel):
    """Platform feature flags exposed to frontend"""
    cloud_agent_creation_enabled: bool = False

class BoundAgentContext(BaseModel):
    """Returned when the credential is bound to an agent."""
    agent_id: str
    agent_name: str
    default_space_id: str | None = None
    default_space_name: str | None = None
    allowed_spaces: list[dict] | None = None

class CredentialScope(BaseModel):
    """PAT scope info — only present when authenticated via credential."""
    agent_scope: str  # 'all', 'user', 'agents'
    allowed_agent_ids: list[str] | None = None

class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    username: str
    role: str
    platform_features: PlatformFeatures | None = None
    bound_agent: BoundAgentContext | None = None
    credential_scope: CredentialScope | None = None

class MessageCreate(BaseModel):
    content: str
    channel: str = Field(default="main", max_length=50)
    parent_id: str | None = None
    metadata: dict | None = None


class ExchangeRequest(BaseModel):
    """POST /auth/exchange request body (AUTH-SPEC-001 §9.1)."""
    requested_token_class: str = Field(..., description="user_access | user_admin | agent_access")
    audience: str = Field(default="ax-api", description="Target audience: ax-api or ax-mcp")
    scope: str = Field(..., description="Space-separated scope list")
    requested_ttl: int | None = Field(default=None, description="Requested TTL in seconds")
    agent_id: str | None = Field(default=None, description="Required for agent_access (existing agent)")
    agent_name: str | None = Field(default=None, description="For enrollment: create agent with this name and bind")
    resource: str | None = Field(default=None, description="RFC 8707 resource URI (maps to audience)")


class ExchangeResponse(BaseModel):
    """POST /auth/exchange response (AUTH-SPEC-001 §9.5)."""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str
    token_class: str
    agent_id: str | None = None
    agent_name: str | None = None










# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@legacy_router.post("/exchange", response_model=ExchangeResponse)
async def exchange_token(
    body: ExchangeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Exchange a PAT for a short-lived JWT (AUTH-SPEC-001 §9).

    The PAT is provided in the Authorization: Bearer header.
    This is the ONLY endpoint that accepts PATs directly.
    Runtime JWTs cannot call this endpoint (enforced by parse_token requiring axp_ prefix).
    """
    from ...core.credential_service import authenticate_credential, parse_token
    from ...core.token_exchange import ExchangeError, validate_exchange_request
    from ...core.ax_jwt import mint_exchange_jwt
    from ...core.redis_client import redis_client as _redis

    client_ip = request.client.host if request.client else "unknown"

    # Extract bearer token
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"error": "invalid_credential", "message": "Missing Bearer token"})
    token = auth_header[7:].strip()

    # Must be a PAT, not a JWT (hard rule §3.1, §3.4)
    parsed = parse_token(token)
    if parsed is None:
        raise HTTPException(status_code=401, detail={"error": "invalid_credential", "message": "Token is not a valid PAT"})

    # Authenticate the PAT (verifies hash, expiration, revocation, bound agent status)
    # authenticate_credential already checks revoked_at and expires_at,
    # satisfying revocation semantics v3 §11: future exchanges blocked immediately.
    try:
        principal = await authenticate_credential(token, db)
    except HTTPException as exc:
        logger.info("EXCHANGE_AUTH_FAIL reason=%s client_ip=%s", exc.detail, client_ip)
        # Rate limit: track exchange failures per PAT key_id (§12.2: 10/5min)
        parsed_for_rl = parse_token(token)
        if parsed_for_rl and _redis:
            rl_key = f"exchange_fail:{parsed_for_rl[1]}"
            try:
                count = await _redis.incr(rl_key)
                if count == 1:
                    await _redis.expire(rl_key, 300)  # 5 minute window
                if count > 10:
                    logger.warning("EXCHANGE_RATE_LIMIT key_id=%s count=%d client_ip=%s", parsed_for_rl[1], count, client_ip)
                    raise HTTPException(status_code=429, detail={"error": "rate_limited", "message": "Too many exchange failures. Try again later."})
            except HTTPException:
                raise
            except Exception:
                pass  # Redis failure shouldn't block auth
        raise HTTPException(status_code=401, detail={"error": "invalid_credential", "message": str(exc.detail)})

    # Derive PAT class from DB state, not token prefix (Codex P1: prefix is mutable)
    # bound_agent_id OR agent_scope=unbound/agents means agent PAT class
    pat_type_char = "a" if (principal.bound_agent_id or principal.agent_scope in ("unbound", "agents")) else "u"

    # Validate exchange parameters against the spec rules
    try:
        validated = validate_exchange_request(
            pat_type_char=pat_type_char,
            requested_token_class=body.requested_token_class,
            requested_scope=body.scope,
            requested_audience=body.audience,
            requested_ttl=body.requested_ttl,
            agent_id=body.agent_id,
            agent_name=body.agent_name,
            resource=body.resource,
            pat_audience=principal.audience,
        )
    except ExchangeError as exc:
        logger.info(
            "EXCHANGE_VALIDATION_FAIL error=%s token_class=%s scope=%s client_ip=%s",
            exc.error_code, body.requested_token_class, body.scope, client_ip,
        )
        raise HTTPException(status_code=exc.http_status, detail={"error": exc.error_code, "message": exc.detail})

    # For agent_access: bind to existing agent or create+bind via enrollment
    agent_id_for_token = None
    delegated_by = None
    if validated.token_class == "agent_access":
        from uuid import UUID as _UUID
        from sqlalchemy import select as _select, text as _text
        from ...models.agent import Agent as _Agent

        if principal.bound_agent_id:
            # Already bound — agent_id must match
            if not body.agent_id:
                raise HTTPException(status_code=400, detail={"error": "agent_not_found", "message": "agent_id is required for agent_access"})
            if str(principal.bound_agent_id) != body.agent_id:
                raise HTTPException(status_code=422, detail={"error": "binding_not_allowed", "message": "agent_id does not match bound agent"})
            agent_id_for_token = str(principal.bound_agent_id)

        elif principal.agent_scope in ("unbound", "agents"):
            # Enrollment flow — create agent if agent_name provided, or bind to existing agent_id
            if body.agent_name and not body.agent_id:
                # Auto-registration: create agent + bind in one operation
                await db.execute(_text("SELECT set_config('app.is_privileged', 'true', true)"))
                try:
                    # Check if agent with this name already exists in the owner's space
                    from app.core.agent_space import agents_in_space_subquery
                    existing = await db.execute(
                        _select(_Agent).where(
                            _Agent.name == body.agent_name,
                            _Agent.id.in_(agents_in_space_subquery(principal.space_id)),
                        )
                    )
                    agent = existing.scalar_one_or_none()
                    if agent:
                        # Agent exists — bind to it (idempotent)
                        agent_uuid = agent.id
                        logger.info("ENROLLMENT_EXISTING agent=%s name=%s", agent.id, body.agent_name)
                    else:
                        # Create new agent with required ownership fields
                        import uuid as _uuid_mod
                        agent_uuid = _uuid_mod.uuid4()
                        new_agent = _Agent(
                            id=agent_uuid,
                            name=body.agent_name,
                            space_id=principal.space_id,
                            user_id=principal.principal_id,
                            owner_user_id=principal.principal_id,
                            owner_space_id=principal.space_id,
                            home_space_id=principal.space_id,
                            created_by_user_id=principal.principal_id,
                            status="active",
                            agent_type="on_demand",
                            origin="cli",
                        )
                        db.add(new_agent)
                        await db.flush()

                        # Grant default space access (owner's home space)
                        from ...models.agent_space_access import AgentSpaceAccess
                        # Use the owner's home space (from their user record)
                        home_space_id = principal.space_id
                        space_access = AgentSpaceAccess(
                            agent_id=agent_uuid,
                            space_id=home_space_id,
                            is_default=True,
                            state="active",
                            attached_by_user_id=principal.principal_id,
                        )
                        db.add(space_access)
                        await db.flush()

                        logger.info(
                            "ENROLLMENT_CREATE agent=%s name=%s space=%s owner=%s client_ip=%s",
                            agent_uuid, body.agent_name, home_space_id, principal.principal_id, client_ip,
                        )
                finally:
                    await db.execute(_text("SELECT set_config('app.is_privileged', 'false', true)"))
                body.agent_id = str(agent_uuid)

            elif body.agent_id:
                # Bind to existing agent by ID
                try:
                    agent_uuid = _UUID(body.agent_id)
                except (ValueError, TypeError):
                    raise HTTPException(status_code=422, detail={"error": "agent_not_found", "message": "Invalid agent_id format"})

                from app.core.agent_space import agents_in_space_subquery
                await db.execute(_text("SELECT set_config('app.is_privileged', 'true', true)"))
                try:
                    agent_result = await db.execute(_select(_Agent).where(
                        _Agent.id == agent_uuid,
                        _Agent.id.in_(agents_in_space_subquery(principal.space_id)),
                    ))
                finally:
                    await db.execute(_text("SELECT set_config('app.is_privileged', 'false', true)"))
                agent = agent_result.scalar_one_or_none()
                if not agent:
                    raise HTTPException(status_code=422, detail={"error": "agent_not_found", "message": "Agent not found"})
            else:
                raise HTTPException(status_code=400, detail={"error": "agent_not_found", "message": "agent_id or agent_name required for agent_access"})

            # Atomic bind: update credential with bound_agent_id
            await db.execute(_text("SELECT set_config('app.is_privileged', 'true', true)"))
            try:
                from ...models.credential import Credential as _Credential
                cred_result = await db.execute(
                    _select(_Credential).where(_Credential.id == principal.credential_id)
                )
                cred = cred_result.scalar_one_or_none()
                if cred and cred.bound_agent_id is not None and str(cred.bound_agent_id) != body.agent_id:
                    # Race condition: already bound to different agent
                    raise HTTPException(status_code=409, detail={"error": "bind_conflict", "message": "Token already bound to a different agent"})
                if cred:
                    cred.bound_agent_id = agent_uuid
                    cred.lifecycle_state = "active"
                    cred.agent_scope = "agents"
                    cred.allowed_agent_ids = [body.agent_id]
                    await db.flush()
                    await db.commit()
            finally:
                await db.execute(_text("SELECT set_config('app.is_privileged', 'false', true)"))

            agent_id_for_token = body.agent_id
            logger.info(
                "ENROLLMENT_BIND credential=%s agent=%s client_ip=%s",
                principal.credential_id, body.agent_id, client_ip,
            )
        else:
            raise HTTPException(status_code=422, detail={"error": "binding_not_allowed", "message": "PAT is not bound to an agent"})

        delegated_by = str(principal.principal_id)

    # Build canonical sub (§5)
    if validated.token_class in ("user_access", "user_admin"):
        sub = f"user:{principal.principal_id}"
    else:
        sub = f"agent:{agent_id_for_token}"

    # Mint the JWT
    access_token = mint_exchange_jwt(
        sub=sub,
        token_class=validated.token_class,
        audience=validated.audience,
        scope=validated.scope,
        ttl_seconds=validated.ttl,
        src_credential_id=str(principal.credential_id),
        owner_user_id=str(principal.principal_id),
        agent_id=agent_id_for_token,
        delegated_by=delegated_by,
        authorized_space_id=(
            str(principal.space_id)
            if validated.token_class == "agent_access"
            else None
        ),
    )

    # Audit log (§12.1)
    logger.info(
        "EXCHANGE_OK sub=%s token_class=%s scope=%s audience=%s ttl=%d "
        "src_credential=%s agent_id=%s client_ip=%s",
        sub, validated.token_class, validated.scope, validated.audience,
        validated.ttl, principal.credential_id, agent_id_for_token, client_ip,
    )

    resp = ExchangeResponse(
        access_token=access_token,
        expires_in=validated.ttl,
        scope=validated.scope,
        token_class=validated.token_class,
    )
    if agent_id_for_token:
        resp.agent_id = agent_id_for_token
        # Always return agent_name — look it up if not from enrollment
        if body.agent_name:
            resp.agent_name = body.agent_name
        else:
            try:
                from sqlalchemy import select as _sa_select
                from ...models.agent import Agent as _AgentModel
                _agent_row = await db.execute(_sa_select(_AgentModel.name).where(_AgentModel.id == _UUID(agent_id_for_token)))
                _name = _agent_row.scalar_one_or_none()
                if _name:
                    resp.agent_name = _name
            except Exception:
                pass
    return resp


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    current_user: User = Depends(get_current_user_from_token),
    db: AsyncSession = Depends(get_db_session),
):
    """Get current user info with platform feature flags.

    When the credential is bound to an agent, returns the agent's
    resolved space context (default space, allowed spaces).
    """
    from app.core.config import get_settings
    from ...core.connection_pools import RedisPool

    settings = get_settings()

    cloud_agent_enabled = settings.cloud_agent_creation_enabled
    try:
        redis_pool = RedisPool.get_client()
        key = "system:settings:cloud_agent_creation_enabled"
        raw_value = await redis_pool.get(key)
        if raw_value is not None:
            value_str = raw_value.decode() if isinstance(raw_value, bytes) else raw_value
            cloud_agent_enabled = value_str.lower() == "true"
    except Exception as e:
        logger.warning(f"Failed to check Redis for cloud_agent_creation_enabled: {e}")

    # Resolve bound agent context if present
    bound_agent = None
    bound_agent_id = getattr(current_user, "_bound_agent_id", None)
    if bound_agent_id:
        try:
            from app.core.agent_space import get_agent_spaces
            from app.models.agent import Agent
            from sqlalchemy import select as sa_select

            agent_result = await db.execute(
                sa_select(Agent).where(Agent.id == bound_agent_id)
            )
            agent = agent_result.scalar_one_or_none()
            if agent:
                spaces = await get_agent_spaces(db, agent.id)
                default_space = next((s for s in spaces if s.is_default), None)

                # Resolve space names
                from app.models.space import Space
                space_ids = [s.space_id for s in spaces]
                if space_ids:
                    org_result = await db.execute(
                        sa_select(Space.id, Space.name).where(
                            Space.id.in_(space_ids)
                        )
                    )
                    space_names = {row.id: row.name for row in org_result.all()}
                else:
                    space_names = {}

                allowed = [
                    {"space_id": str(s.space_id), "name": space_names.get(s.space_id, ""), "is_default": s.is_default}
                    for s in spaces
                ]

                bound_agent = BoundAgentContext(
                    agent_id=str(agent.id),
                    agent_name=agent.name,
                    default_space_id=str(default_space.space_id) if default_space else None,
                    default_space_name=space_names.get(default_space.space_id, "") if default_space else None,
                    allowed_spaces=allowed,
                )
        except Exception as e:
            logger.warning("Failed to resolve bound agent context: %s", e)

    # Build credential scope info if authenticated via PAT
    credential_scope = None
    agent_scope = getattr(current_user, "_credential_agent_scope", None)
    if agent_scope:
        allowed_ids = getattr(current_user, "_credential_allowed_agent_ids", None)
        credential_scope = CredentialScope(
            agent_scope=agent_scope,
            allowed_agent_ids=[str(a) for a in allowed_ids] if allowed_ids else None,
        )

    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        full_name=current_user.full_name,
        username=current_user.username,
        role=current_user.role,
        platform_features=PlatformFeatures(cloud_agent_creation_enabled=cloud_agent_enabled),
        bound_agent=bound_agent,
        credential_scope=credential_scope,
    )




@router.get("/messages")
async def get_user_messages(
    limit: int = Query(200, description="Number of messages to return (server caps to 100)"),
    channel: str | None = Query(None, description="Optional channel filter"),
    hours: float = Query(None, description="Time period in hours to filter messages"),
    since: str = Query(None, description="ISO timestamp - only return messages created after this time"),
    before: str = Query(None, description="ISO timestamp - only return messages created before this time"),
    include_intelligence: bool = Query(False, description="Include AI intelligence scores"),
    media_type: str | None = Query(
        None,
        pattern="^(all|image|video|audio|media)$",
        description="Filter by media type",
    ),
    session: SecureSession = Depends(get_secure_session),
):
    """Get user messages for the dashboard - supports incremental and backward pagination"""
    try:
        query = select(Message).options(selectinload(Message.user), selectinload(Message.agent))

        if include_intelligence:
            query = query.options(selectinload(Message.intelligence_data))

        query = query.where(Message.space_id == session.space_id)

        if channel:
            query = query.where(Message.channel == channel)

        def _media_content_clause(types: set[str]):
            content = Message.content
            clauses = []
            if "image" in types:
                clauses.append(content.ilike("%![%"))
                clauses.append(content.op("~*")(r"\.(png|jpe?g|gif|webp|bmp|svg)(\?|\s|$)"))
            if "audio" in types:
                clauses.append(content.op("~*")(r"\.(mp3|wav|m4a|ogg|flac|aac)(\?|\s|$)"))
                clauses.append(content.ilike("%soundcloud.com%"))
                clauses.append(content.ilike("%spotify.com%"))
            if "video" in types:
                clauses.append(content.op("~*")(r"\.(mp4|mov|webm|mkv|avi|m4v)(\?|\s|$)"))
                clauses.append(content.ilike("%youtube.com%"))
                clauses.append(content.ilike("%youtu.be%"))
                clauses.append(content.ilike("%vimeo.com%"))
            return or_(*clauses) if clauses else None

        before_dt = None
        before_id = None
        if before:
            before_raw = before
            if "|" in before:
                before_raw, before_id_raw = before.split("|", 1)
                try:
                    before_id = uuid.UUID(before_id_raw)
                except (ValueError, TypeError, AttributeError):
                    before_id = None
            try:
                before_dt = datetime.fromisoformat(before_raw.replace("Z", "+00:00"))
                if before_id:
                    query = query.where(
                        or_(
                            Message.created_at < before_dt,
                            and_(Message.created_at == before_dt, Message.id < before_id),
                        )
                    )
                else:
                    query = query.where(Message.created_at < before_dt)
            except (ValueError, AttributeError):
                pass

        if not before or before_dt is None:
            if since:
                try:
                    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                    query = query.where(Message.created_at > since_dt)
                except (ValueError, AttributeError):
                    pass
            elif hours:
                time_threshold = datetime.utcnow() - timedelta(hours=hours)
                query = query.where(Message.created_at >= time_threshold)

        if media_type and media_type != "all":
            if media_type == "media":
                media_types = {"image", "video", "audio"}
                content_clause = _media_content_clause(media_types)
                if content_clause is not None:
                    query = query.where(or_(Message.message_type.in_(media_types), content_clause))
                else:
                    query = query.where(Message.message_type.in_(media_types))
            else:
                content_clause = _media_content_clause({media_type})
                if content_clause is not None:
                    query = query.where(or_(Message.message_type == media_type, content_clause))
                else:
                    query = query.where(Message.message_type == media_type)

        safe_limit = max(1, min(int(limit or 1), 100))
        query = query.order_by(Message.created_at.desc(), Message.id.desc()).limit(safe_limit + 1)

        result = await session.db.execute(query)
        messages = result.scalars().all()

        has_more = len(messages) > safe_limit
        if has_more:
            messages = messages[:safe_limit]

        next_cursor = None
        if messages and has_more:
            oldest_message = messages[-1]
            if oldest_message.created_at:
                next_cursor = f"{oldest_message.created_at.isoformat()}|{oldest_message.id}"

        server_time = datetime.utcnow()
        latest_timestamp = None
        if messages:
            latest_timestamp = max(msg.created_at for msg in messages)

        message_list = []
        for msg in messages:
            if msg.agent is not None:
                username = msg.agent.name or "Unknown Agent"
                agent_type = "agent"
            elif msg.user is not None:
                username = msg.user.username or "Unknown User"
                agent_type = "user"
            else:
                username = "Unknown"
                agent_type = "user"

            message_data = {
                "id": str(msg.id),
                "content": msg.content,
                "username": username,
                "channel": msg.channel or "general",
                "uploaded_at": msg.created_at.isoformat(),
                "created_at": msg.created_at.isoformat(),
                "agent_id": str(msg.agent_id) if msg.agent_id else None,
                "user_id": str(msg.user_id) if msg.user_id else None,
                "parent_id": str(msg.parent_id) if msg.parent_id else None,
                "message_type": msg.message_type or "message",
                "metadata": msg.message_metadata or {},
                "agent_type": agent_type,
                "ai_summary": msg.ai_summary,
            }

            if include_intelligence:
                intel = getattr(msg, "intelligence_data", None)
                if intel and agent_type == "agent":
                    message_data["quality_score"] = intel.quality_score
                    message_data["spam_score"] = intel.spam_score
                    message_data["toxicity_score"] = intel.toxicity_score
                    message_data["security_risk"] = intel.security_risk
                else:
                    message_data["quality_score"] = None
                    message_data["spam_score"] = None
                    message_data["toxicity_score"] = None
                    message_data["security_risk"] = None

            message_list.append(message_data)

        return {
            "posts": message_list,
            "messages": message_list,
            "count": len(message_list),
            "total": len(message_list),
            "user_isolated": True,
            "server_time": server_time.isoformat(),
            "latest_timestamp": latest_timestamp.isoformat() if latest_timestamp else None,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    except Exception as e:
        print(f"Error fetching messages: {e}")
        return {
            "posts": [],
            "messages": [],
            "count": 0,
            "total": 0,
            "user_isolated": True,
            "has_more": False,
            "next_cursor": None,
        }


@router.post("/messages")
async def create_message(
    message: MessageCreate,
    session: SecureSession = Depends(get_secure_session),
):
    """Create a new message"""
    try:
        import re

        if session.is_agent and session.agent_id:
            actor = Actor(
                id=session.agent_id, type="agent", space_id=session.space_id,
                capabilities={CAP_MESSAGES_SEND}
            )
            display_name = session.agent_name or "Agent"
        else:
            actor = Actor(
                id=session.user.id, type="human", space_id=session.space_id,
                capabilities={CAP_MESSAGES_SEND}
            )
            display_name = session.user.username or session.user.full_name or "User"

        svc = MessagesService(db=session.db, redis_client=redis_client, sse_broker=redis_sse_broker)

        content_stripped = message.content.strip()

        # Multi-emoji support: split and send each as a reaction via service
        if message.parent_id and len(content_stripped.replace(" ", "")) > 1:
            emoji_pattern = r"[\U0001F300-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\U0001F1E0-\U0001F1FF]"
            individual_emojis = re.findall(emoji_pattern, content_stripped)
            if individual_emojis and "".join(individual_emojis) == content_stripped.replace(" ", ""):
                created = 0
                first_msg_id = None
                for emoji in individual_emojis:
                    msg = await svc.send(
                        actor=actor,
                        content=emoji,
                        channel=message.channel,
                        message_type="message",
                        parent_id=message.parent_id,
                        metadata=message.metadata,
                        author_display_name=display_name,
                        adapter="mcp" if session.is_agent else "api",
                    )
                    if not first_msg_id:
                        first_msg_id = str(msg.id)
                    created += 1
                return {
                    "id": first_msg_id,
                    "content": message.content,
                    "reactions_created": created,
                    "emojis": individual_emojis,
                    "message": f"Created {created} reactions",
                }

        msg = await svc.send(
            actor=actor,
            content=message.content,
            channel=message.channel,
            message_type="message",
            parent_id=message.parent_id,
            metadata=message.metadata,
            author_display_name=display_name,
            adapter="mcp" if session.is_agent else "api",
        )

        return {
            "id": str(msg.id),
            "content": msg.content,
            "channel": msg.channel,
            "message_type": msg.message_type,
            "parent_id": str(msg.parent_id) if msg.parent_id else None,
            "timestamp": msg.created_at.isoformat() if hasattr(msg, "created_at") and msg.created_at else None,
            "status": "sent",
        }

    except Exception as e:
        print(f"Error creating message: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create message: {e!s}"
        )


def _get_cookie_security() -> bool:
    """Get cookie security setting based on environment"""
    settings = get_settings()
    return settings.environment.lower() == "production"


@router.post("/logout")
async def logout(request: Request, response: Response, system: SystemSession = Depends(get_system_session)):
    """Logout user by revoking refresh token and clearing httpOnly cookie"""
    try:
        from ...core.security import revoke_refresh_token

        refresh_token_value = request.cookies.get("refresh_token")
        success = False

        if refresh_token_value:
            success = await revoke_refresh_token(system.db, refresh_token_value)

        response.delete_cookie(
            key="refresh_token",
            httponly=True,
            secure=_get_cookie_security(),
            samesite="lax" if _get_cookie_security() else "strict",
            domain=".paxai.app" if _get_cookie_security() else None,
            path="/",
        )
        response.delete_cookie(
            key="ax_session",
            httponly=True,
            secure=_get_cookie_security(),
            samesite="lax" if _get_cookie_security() else "strict",
            domain=".paxai.app" if _get_cookie_security() else None,
            path="/",
        )

        return {"message": "Logged out successfully", "revoked": success}

    except Exception as e:
        print(f"Logout error: {e}")
        response.delete_cookie(
            key="refresh_token",
            httponly=True,
            secure=_get_cookie_security(),
            samesite="lax" if _get_cookie_security() else "strict",
            domain=".paxai.app" if _get_cookie_security() else None,
            path="/",
        )
        response.delete_cookie(
            key="ax_session",
            httponly=True,
            secure=_get_cookie_security(),
            samesite="lax" if _get_cookie_security() else "strict",
            domain=".paxai.app" if _get_cookie_security() else None,
            path="/",
        )
        return {"message": "Logged out successfully", "revoked": False}


@router.get("/beta-status")
async def get_beta_status():
    """Get current beta configuration status"""
    beta_config = get_beta_config()
    return {
        "status": "active",
        "configuration": beta_config.get_beta_status(),
        "features": {
            "invite_only_registration": beta_config.INVITE_ONLY,
            "agent_limits_enforced": True,
            "gamification_ready": beta_config.ENABLE_GAMIFICATION,
            "credits_system_ready": beta_config.ENABLE_CREDITS,
        },
        "limits": {
            "max_users": beta_config.MAX_USERS,
            "default_agent_limit": beta_config.AGENT_LIMIT_DEFAULT,
            "admin_unlimited": beta_config.ADMIN_UNLIMITED,
        },
    }
