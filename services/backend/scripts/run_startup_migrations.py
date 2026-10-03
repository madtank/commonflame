#!/usr/bin/env python3
"""Run startup migrations under a database advisory lock.

Both the API and background workers use docker-entrypoint.sh. In ECS they can
start at nearly the same time, so repair_alembic_state.py and Alembic must run
as one serialized critical section.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.repair_alembic_state import (  # noqa: E402
    RepairResult,
    repair_known_alembic_state,
    resolve_database_url,
)


MIGRATION_LOCK_KEY = 840220260524


def _print_repair_result(result: RepairResult) -> None:
    if result.action == "repaired":
        print(f"MIGRATION_REPAIR_APPLIED state={result.state} {result.detail}".strip())
        return

    if result.action == "noop":
        suffix = f" detail={result.detail}" if result.detail else ""
        print(f"MIGRATION_REPAIR_NOOP state={result.state}{suffix}")
        return

    print(f"MIGRATION_REPAIR_ERROR state={result.state} detail={result.detail}", file=sys.stderr)


def _run_alembic_upgrade() -> int:
    return subprocess.run(["alembic", "upgrade", "heads"], check=False).returncode


def run_startup_migrations(database_url: str | None = None) -> int:
    database_url = database_url or resolve_database_url()
    engine = create_engine(database_url)
    lock_acquired = False

    try:
        with engine.connect() as conn:
            if conn.dialect.name == "postgresql":
                print("🔒 Acquiring database migration lock...")
                conn.execute(text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": MIGRATION_LOCK_KEY})
                lock_acquired = True
                print("✅ Database migration lock acquired")

            try:
                result = repair_known_alembic_state(database_url)
                _print_repair_result(result)
                if result.action == "error":
                    return 1

                return _run_alembic_upgrade()
            finally:
                if lock_acquired:
                    conn.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": MIGRATION_LOCK_KEY},
                    )
                    print("🔓 Database migration lock released")
    finally:
        engine.dispose()


def main() -> int:
    try:
        return run_startup_migrations()
    except (RuntimeError, SQLAlchemyError) as exc:
        print(f"MIGRATION_ERROR state=unhandled detail={exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
