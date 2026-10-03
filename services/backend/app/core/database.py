"""
Database configuration and session management
Following cipher's recommendations: asyncpg with connection pooling

Connection strategy (Private IP migration):
  1. If DATABASE_URL_PRIVATE is set and DB_PREFER_PRIVATE != false,
     try to connect via Private IP first (VPC, no public exposure).
  2. If Private IP is unreachable, fall back to DATABASE_URL (unix socket
     via Cloud SQL Auth Proxy — the current production path).
  3. The active connection method is exposed via DB_CONNECTION_METHOD for
     health checks and observability.

Rollback: Set DB_PREFER_PRIVATE=false or unset DATABASE_URL_PRIVATE.
"""
# @ax:tag area=backend component=database_config tech=sqlalchemy,asyncpg,fastapi guide=backend/AGENT.md
import os
import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine
)
from sqlalchemy.pool import NullPool
from sqlalchemy import text

from ..models import Base
from .bootstrap_schema import prepare_postgres_bootstrap

logger = logging.getLogger(__name__)

# Database configuration using centralized settings
from .config import get_settings

settings = get_settings()

# Track which connection path is active (for health endpoint reporting)
DB_CONNECTION_METHOD = "default"  # "private_ip" | "cloudsql_proxy" | "default"


def _normalize_url(url: str) -> str:
    """Convert sync postgres:// URL to async postgresql+asyncpg:// for SQLAlchemy."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _describe_url(url: str) -> str:
    """Extract a safe description of the connection target for logging."""
    import re
    if "?host=" in url:
        match = re.search(r'host=([^&]+)', url)
        return f"unix:{match.group(1)}" if match else "unix_socket"
    match = re.search(r'@([^:/]+)', url)
    return match.group(1) if match else "unknown"


def _build_engine_kwargs() -> dict:
    """Build SQLAlchemy engine keyword arguments with pool settings."""
    kwargs = {
        "pool_pre_ping": True,
        "echo": os.getenv("ENABLE_SQL_LOGGING", "false").lower() == "true",
    }
    # --- DB_POOL_SETTINGS (env-driven) ---
    pool_size = int(os.getenv("DB_POOL_SIZE", "50"))
    max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "30"))
    pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
    pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "3600"))
    pool_use_lifo = os.getenv("DB_POOL_USE_LIFO", "true").lower() == "true"
    kwargs.update({
        "pool_size": pool_size,
        "max_overflow": max_overflow,
        "pool_timeout": pool_timeout,
        "pool_recycle": pool_recycle,
        "pool_use_lifo": pool_use_lifo,
    })
    return kwargs


def _select_database_url() -> str:
    """
    Choose the best database URL with Private IP preference and fallback.

    Priority:
      1. DATABASE_URL_PRIVATE (if set and DB_PREFER_PRIVATE != false)
      2. DATABASE_URL (current unix socket / Cloud SQL Auth Proxy path)

    At module load time we cannot do an async connection test, so this
    just selects the URL. The async verify_connection() function (called
    on app startup) will test connectivity and swap to fallback if needed.
    """
    global DB_CONNECTION_METHOD

    primary_url = _normalize_url(settings.database_url)
    private_url = settings.database_url_private

    if private_url and settings.db_prefer_private:
        private_url = _normalize_url(private_url)
        logger.info(
            "🔒 Private IP URL configured — will prefer VPC connection (%s)",
            _describe_url(private_url),
        )
        DB_CONNECTION_METHOD = "private_ip"
        return private_url

    if private_url and not settings.db_prefer_private:
        logger.info("ℹ️  DB_PREFER_PRIVATE=false — using standard connection")

    DB_CONNECTION_METHOD = "cloudsql_proxy"
    logger.info("🔌 Using standard database connection (%s)", _describe_url(primary_url))
    return primary_url


# Resolve the active URL and build the engine
DATABASE_URL = _select_database_url()
_FALLBACK_URL = _normalize_url(settings.database_url)  # always keep the original as fallback

engine_kwargs = _build_engine_kwargs()

# Skip pool sizing for SQLite (test environments)
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs = {k: v for k, v in engine_kwargs.items()
                     if k in ("pool_pre_ping", "echo")}

engine = create_async_engine(DATABASE_URL, **engine_kwargs)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def verify_connection() -> bool:
    """
    Verify the active database connection on app startup.

    If the primary engine (Private IP) fails, automatically falls back
    to the standard connection (Cloud SQL Auth Proxy) and rebuilds the
    engine + session factory. This runs once during FastAPI lifespan.

    Returns True if a working connection was established.
    """
    global engine, AsyncSessionLocal, DATABASE_URL, DB_CONNECTION_METHOD

    # Test primary connection
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info(
            "✅ Database connection verified (method=%s, target=%s)",
            DB_CONNECTION_METHOD,
            _describe_url(DATABASE_URL),
        )
        return True
    except Exception as e:
        logger.warning(
            "⚠️  Primary database connection failed (method=%s): %s",
            DB_CONNECTION_METHOD, e,
        )

    # If we were trying private IP, fall back to the standard URL
    if DATABASE_URL != _FALLBACK_URL:
        logger.info(
            "🔄 Falling back to standard connection (%s)",
            _describe_url(_FALLBACK_URL),
        )
        DATABASE_URL = _FALLBACK_URL
        DB_CONNECTION_METHOD = "cloudsql_proxy"

        await engine.dispose()
        engine = create_async_engine(DATABASE_URL, **_build_engine_kwargs())
        AsyncSessionLocal = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info(
                "✅ Fallback database connection verified (method=%s, target=%s)",
                DB_CONNECTION_METHOD,
                _describe_url(DATABASE_URL),
            )
            return True
        except Exception as e2:
            logger.error("❌ Fallback database connection also failed: %s", e2)
            return False

    logger.error("❌ Database connection failed with no fallback available")
    return False


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Database session dependency for FastAPI
    Following cipher's recommended pattern: async with get_db_session()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_tables():
    """Create all tables (for initial setup)"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def bootstrap_schema():
    """Prepare extensions/types and create all ORM-managed tables."""
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: prepare_postgres_bootstrap(sync_conn, Base.metadata))


async def drop_tables():
    """Drop all tables (for testing/reset)"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def test_connection() -> bool:
    """Test database connection"""
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT 1"))
            return result.scalar() == 1
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False


async def set_rls_context(
    session: AsyncSession,
    user_id: str,
    space_id: str,
    agent_id: str | None = None,
) -> None:
    """
    Set PostgreSQL session variables for Row-Level Security.

    This MUST be called at the start of each request before any database
    queries. It sets the 'app.current_space_id', 'app.current_user_id', and
    optionally 'app.current_agent_id' session variables that RLS policies
    reference.

    Args:
        session: The AsyncSession for the current request
        user_id: UUID string of the current user or agent
        space_id: UUID string of the space to scope queries to
        agent_id: UUID string of the agent making the request (required for
                  guest agent RLS policies; None for user requests)

    Security:
        - Without this call, RLS policies will return NO ROWS (fail closed)
        - This prevents cross-space data leakage at the database level
        - Even buggy application code cannot access wrong-space data
        - Guest RLS policies require app.current_agent_id to enforce
          per-agent channel visibility (guest_accessible flag)

    Example:
        async with get_db_session() as session:
            await set_rls_context(session, user_id=str(user.id), space_id=str(user.space_id))
            # Now all queries are automatically filtered by space_id
            result = await session.execute(select(Task))  # Only returns user's space tasks
    """
    # Use set_config with is_local=true so the setting only applies to this transaction
    # The third parameter 'true' makes it transaction-local, which is safer
    await session.execute(
        text("SELECT set_config('app.current_space_id', :space_id, true)"),
        {"space_id": space_id}
    )
    await session.execute(
        text("SELECT set_config('app.current_user_id', :user_id, true)"),
        {"user_id": user_id}
    )
    # Set agent_id for guest RLS policies. Empty string when not set so policies
    # can check: current_setting('app.current_agent_id', true) != '' to detect
    # guest context without raising an exception on missing setting.
    await session.execute(
        text("SELECT set_config('app.current_agent_id', :agent_id, true)"),
        {"agent_id": agent_id or ""}
    )


async def get_db_info() -> dict:
    """Get database information for health checks, including connection method."""
    try:
        async with AsyncSessionLocal() as session:
            # Get PostgreSQL version
            result = await session.execute(text("SELECT version()"))
            version = result.scalar()

            # Get current database name
            result = await session.execute(text("SELECT current_database()"))
            database = result.scalar()

            # Get current user
            result = await session.execute(text("SELECT current_user"))
            user = result.scalar()

            # Get server IP to confirm which path we're using
            result = await session.execute(text("SELECT inet_server_addr()"))
            server_addr = result.scalar()

            pool_info = {}
            try:
                pool_info = {
                    "pool_size": getattr(engine.pool, "size", lambda: None)(),
                    "checked_out": getattr(engine.pool, "checkedout", lambda: None)(),
                }
            except Exception:
                pool_info = {"pool_size": None, "checked_out": None}

            return {
                "status": "healthy",
                "database": database,
                "user": user,
                "version": version,
                "connection_method": DB_CONNECTION_METHOD,
                "server_addr": str(server_addr) if server_addr else "unix_socket",
                **pool_info,
            }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "connection_method": DB_CONNECTION_METHOD,
        }
