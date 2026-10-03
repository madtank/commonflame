"""
Optimized connection pooling for production
Handles both database and Redis connections efficiently
"""

import os
import logging
import socket
from contextlib import asynccontextmanager
from typing import Optional
import yaml
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, AsyncEngine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool, QueuePool
import redis.asyncio as redis
from redis import exceptions as redis_exceptions
from redis.asyncio.connection import ConnectionPool

logger = logging.getLogger(__name__)

# Load production config if available
config_path = Path(__file__).parent.parent.parent / "config" / "production.yaml"
if config_path.exists():
    with open(config_path) as f:
        PROD_CONFIG = yaml.safe_load(f)
else:
    PROD_CONFIG = {}

# Environment variables
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://localhost/axmarketplace")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


class DatabasePool:
    """Optimized database connection pool"""

    _engine: Optional[AsyncEngine] = None
    _session_factory: Optional[sessionmaker] = None

    @classmethod
    def get_engine(cls) -> AsyncEngine:
        """Get or create database engine with optimized pooling"""
        if cls._engine is None:
            db_config = PROD_CONFIG.get("database", {})

            # Production pooling configuration
            if ENVIRONMENT == "production":
                pool_class = QueuePool
                pool_kwargs = {
                    "pool_size": db_config.get("pool_size", 20),
                    "max_overflow": db_config.get("max_overflow", 10),
                    "pool_timeout": db_config.get("pool_timeout", 30),
                    "pool_recycle": db_config.get("pool_recycle", 3600),
                    "pool_pre_ping": db_config.get("pool_pre_ping", True),
                    "echo": False,
                    "echo_pool": False,
                }
            else:
                # Development: smaller pool
                pool_class = QueuePool
                pool_kwargs = {
                    "pool_size": 5,
                    "max_overflow": 5,
                    "pool_timeout": 30,
                    "pool_recycle": 3600,
                    "pool_pre_ping": True,
                    "echo": False,
                }

            # Connection arguments
            connect_args = db_config.get("connect_args", {})
            if ENVIRONMENT == "production":
                # Add production-specific connection args
                connect_args.update({
                    "server_settings": {
                        "application_name": "ax-platform-api",
                        "jit": "off",
                    },
                    "command_timeout": 60,
                    "timeout": 30,
                })

            cls._engine = create_async_engine(
                DATABASE_URL,
                poolclass=pool_class,
                connect_args=connect_args,
                **pool_kwargs
            )

            logger.info(
                f"Database pool initialized: size={pool_kwargs.get('pool_size')}, "
                f"overflow={pool_kwargs.get('max_overflow')}"
            )

        return cls._engine

    @classmethod
    def get_session_factory(cls) -> sessionmaker:
        """Get async session factory"""
        if cls._session_factory is None:
            cls._session_factory = sessionmaker(
                cls.get_engine(),
                class_=AsyncSession,
                expire_on_commit=False,
                autocommit=False,
                autoflush=False,
            )
        return cls._session_factory

    @classmethod
    async def close(cls):
        """Close database connections"""
        if cls._engine:
            await cls._engine.dispose()
            cls._engine = None
            cls._session_factory = None
            logger.info("Database pool closed")

    @classmethod
    @asynccontextmanager
    async def get_session(cls):
        """Get database session with automatic cleanup"""
        async with cls.get_session_factory()() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    @classmethod
    async def health_check(cls) -> bool:
        """Check database connectivity"""
        try:
            async with cls.get_session() as session:
                result = await session.execute("SELECT 1")
                return result.scalar() == 1
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False


class RedisPool:
    """Optimized Redis connection pool"""

    _pool: Optional[ConnectionPool] = None
    _client: Optional[redis.Redis] = None

    @classmethod
    def get_pool(cls) -> ConnectionPool:
        """Get or create Redis connection pool"""
        if cls._pool is None:
            redis_config = PROD_CONFIG.get("redis", {})

            # Production pool configuration
            if ENVIRONMENT == "production":
                pool_kwargs = {
                    "max_connections": int(redis_config.get("max_connections", 50)),
                    "decode_responses": bool(redis_config.get("decode_responses", True)),
                    "socket_timeout": float(redis_config.get("socket_timeout", 5)),
                    "socket_connect_timeout": float(redis_config.get("socket_connect_timeout", 5)),
                    "socket_keepalive": bool(redis_config.get("socket_keepalive", True)),
                    # socket_keepalive_options requires socket constants as keys, not strings
                    # Only set if the platform supports these options (Linux)
                    **({"socket_keepalive_options": {
                        socket.TCP_KEEPIDLE: 120,
                        socket.TCP_KEEPINTVL: 30,
                        socket.TCP_KEEPCNT: 3,
                    }} if hasattr(socket, 'TCP_KEEPIDLE') else {}),
                    "retry_on_timeout": bool(redis_config.get("retry_on_timeout", True)),
                    "health_check_interval": 30,
                }
            else:
                # Development: smaller pool
                pool_kwargs = {
                    "max_connections": 10,
                    "decode_responses": True,
                    "socket_timeout": 5,
                    "socket_connect_timeout": 5,
                }

            cls._pool = redis.ConnectionPool.from_url(
                REDIS_URL,
                **pool_kwargs
            )

            logger.info(
                f"Redis pool initialized: max_connections={pool_kwargs.get('max_connections')}"
            )

        return cls._pool

    @classmethod
    def get_client(cls) -> redis.Redis:
        """Get Redis client with connection pool"""
        if cls._client is None:
            cls._client = redis.Redis(
                connection_pool=cls.get_pool(),
                auto_close_connection_pool=False,
            )
        return cls._client

    @classmethod
    async def close(cls):
        """Close Redis connections"""
        if cls._client:
            await cls._client.close()
            cls._client = None
        if cls._pool:
            await cls._pool.disconnect()
            cls._pool = None
        logger.info("Redis pool closed")

    @classmethod
    async def _reset_pool(cls):
        """Reset the connection pool to recover from parser errors"""
        logger.warning("Resetting Redis connection pool due to parser error...")
        try:
            if cls._client:
                try:
                    await cls._client.close()
                except Exception:
                    pass  # Ignore close errors
                cls._client = None
            if cls._pool:
                try:
                    await cls._pool.disconnect()
                except Exception:
                    pass  # Ignore disconnect errors
                cls._pool = None
            # Recreate
            cls.get_client()
            logger.info("Redis connection pool reset successfully")
        except Exception as e:
            logger.error(f"Failed to reset Redis pool: {e}")
            raise

    @classmethod
    async def health_check(cls) -> bool:
        """Check Redis connectivity"""
        try:
            await cls.execute('ping')
            return True
        except (AttributeError, redis_exceptions.ConnectionError, redis_exceptions.TimeoutError) as e:
            logger.error(f"Redis health check failed ({type(e).__name__}): {e}")
            return False
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False

    @classmethod
    @asynccontextmanager
    async def get_lock(cls, key: str, timeout: int = 10):
        """Distributed lock using Redis"""
        client = cls.get_client()
        lock = client.lock(f"lock:{key}", timeout=timeout)

        try:
            acquired = await lock.acquire(blocking=True, blocking_timeout=5)
            if acquired:
                yield lock
            else:
                raise TimeoutError(f"Could not acquire lock for {key}")
        finally:
            if acquired:
                await lock.release()

    @classmethod
    async def execute(cls, command_name: str, *args, **kwargs):
        """
        Execute a Redis command with automatic retry for stale connections.
        Handles the specific 'AsyncRESP2Parser' object has no attribute '_connected' error
        common in serverless environments (Cloud Run).
        """
        retries = 1

        for attempt in range(retries + 1):
            try:
                client = cls.get_client()
                method = getattr(client, command_name)

                # Execute the command
                # Note: Most redis-py async methods are coroutines
                return await method(*args, **kwargs)

            except (AttributeError, redis_exceptions.ConnectionError) as e:
                # Check for specific redis-py parser bug or generic connection error
                is_parser_bug = isinstance(e, AttributeError) and "_connected" in str(e)
                is_conn_error = isinstance(e, redis_exceptions.ConnectionError)

                if (is_parser_bug or is_conn_error) and attempt < retries:
                    logger.warning(
                        f"Redis connection stale (attempt {attempt+1}/{retries+1}), "
                        f"resetting pool. Error: {e}"
                    )
                    await cls.close()  # Close old pool
                    # Next loop iteration will call get_client() which creates new pool
                    continue

                # If we ran out of retries or it's a different error, raise it
                raise e


class ConnectionPoolManager:
    """Manage all connection pools"""

    @classmethod
    async def initialize(cls):
        """Initialize all connection pools"""
        # Warm up database pool
        DatabasePool.get_engine()

        # Warm up Redis pool
        RedisPool.get_pool()

        # Perform health checks
        db_healthy = await DatabasePool.health_check()
        redis_healthy = await RedisPool.health_check()

        logger.info(
            f"Connection pools initialized: DB={db_healthy}, Redis={redis_healthy}"
        )

        return db_healthy and redis_healthy

    @classmethod
    async def shutdown(cls):
        """Shutdown all connection pools"""
        await DatabasePool.close()
        await RedisPool.close()
        logger.info("All connection pools closed")

    @classmethod
    async def health_check(cls) -> dict:
        """Check health of all connections"""
        return {
            "database": await DatabasePool.health_check(),
            "redis": await RedisPool.health_check(),
        }


# Convenience exports
get_db_session = DatabasePool.get_session
get_redis_client = RedisPool.get_client
