from logging.config import fileConfig
import sys
import os
from pathlib import Path

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Add the backend directory to the path so we can import our models
sys.path.append(str(Path(__file__).parent.parent))

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all models here for 'autogenerate' support
# We'll create these models next
try:
    from app.models import Base
    target_metadata = Base.metadata
except ImportError:
    # Models not yet created, use None for now
    target_metadata = None

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    # Prioritize environment variable over alembic.ini configuration
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Get configuration section and override with environment variable if present
    configuration = config.get_section(config.config_ini_section, {})

    # Prioritize MCP_DATABASE_URL (sync driver) for Alembic migrations
    # MCP_DATABASE_URL uses postgresql:// (psycopg2), DATABASE_URL uses postgresql+asyncpg://
    if os.environ.get("MCP_DATABASE_URL"):
        configuration["sqlalchemy.url"] = os.environ.get("MCP_DATABASE_URL")
    elif os.environ.get("DATABASE_URL"):
        # Fallback to DATABASE_URL but strip asyncpg driver
        db_url = os.environ.get("DATABASE_URL").replace("postgresql+asyncpg://", "postgresql://")
        configuration["sqlalchemy.url"] = db_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
