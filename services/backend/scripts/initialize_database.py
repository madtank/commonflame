"""Bootstrap a blank database or migrate an existing one under one lock."""
import asyncio
import subprocess

from sqlalchemy import create_engine, text
from app.core.secrets import get_mcp_database_url
from app.core.bootstrap_schema import (
    create_bootstrap_sql_objects, requires_postgres_bootstrap, seed_system_principals,
)


def main():
    engine = create_engine(get_mcp_database_url())
    with engine.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(840220261002)"))
        try:
            with engine.connect() as db:
                fresh = requires_postgres_bootstrap(db)
            if fresh:
                from app.core.database import bootstrap_schema
                asyncio.run(bootstrap_schema())
                subprocess.run(["alembic", "stamp", "heads"], check=True)
            else:
                subprocess.run(["alembic", "upgrade", "heads"], check=True)
            with engine.begin() as db:
                create_bootstrap_sql_objects(db)
                seed_system_principals(db)
            print("Database ready")
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(840220261002)"))
    engine.dispose()


if __name__ == "__main__":
    main()
