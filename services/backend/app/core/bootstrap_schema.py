"""Bootstrap helpers for fresh database installations.

ORM-managed core tables (users, agents, spaces) are NOT created by Alembic
migrations — migrations only ALTER them.  On a blank database these tables must
be created via ``metadata.create_all()`` before the migration chain can run.

This module also seeds the reserved system principals (system space + system
user) that the platform requires for agent auto-provisioning.  Seeding runs on
every container startup so new environments work out of the box.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import Enum as SqlEnum, MetaData, inspect, text

logger = logging.getLogger(__name__)

CORE_BOOTSTRAP_TABLES = ("users", "agents", "spaces")
ALEMBIC_VERSION_TABLE = "alembic_version"
ALEMBIC_VERSION_LENGTH = 255

# System principal constants — must match app.core.system_agents and
# app.services.space_agent_service.
SYSTEM_SPACE_ID = "00000000-0000-0000-0000-000000000000"
SYSTEM_USER_ID = "00000000-0000-0000-0000-000000000001"
SYSTEM_SPACE_NAME = "__system__"
SYSTEM_SPACE_SLUG = "__system__"
SYSTEM_USER_EMAIL = "system@internal.ax-platform"
SYSTEM_USER_USERNAME = "__system__"
SYSTEM_USER_PASSWORD_HASH = (
    "$2b$12$IMPOSSIBLE.HASH.NEVER.MATCHES.ANYTHING.SYSTEM.USER.NO.LOGIN"
)


def iter_native_enum_types(metadata: MetaData) -> Iterable[SqlEnum]:
    """Yield unique native enum types referenced by SQLAlchemy metadata."""
    seen: set[tuple[str | None, str | None]] = set()

    for table in metadata.sorted_tables:
        for column in table.columns:
            enum_type = column.type
            if not isinstance(enum_type, SqlEnum):
                continue
            if not getattr(enum_type, "native_enum", True):
                continue

            key = (getattr(enum_type, "schema", None), getattr(enum_type, "name", None))
            if key in seen:
                continue

            seen.add(key)
            yield enum_type


def missing_core_tables(
    inspector,
    required_tables: tuple[str, ...] = CORE_BOOTSTRAP_TABLES,
) -> list[str]:
    """Return core ORM tables that are still missing from the database."""
    return [table_name for table_name in required_tables if not inspector.has_table(table_name)]


def missing_bootstrap_prerequisites(
    inspector,
    required_tables: tuple[str, ...] = CORE_BOOTSTRAP_TABLES,
) -> list[str]:
    """Return missing schema markers that require ORM bootstrap before Alembic."""
    missing = list(missing_core_tables(inspector, required_tables))
    if not inspector.has_table(ALEMBIC_VERSION_TABLE):
        missing.append(ALEMBIC_VERSION_TABLE)
    return missing


def requires_postgres_bootstrap(
    sync_conn,
    required_tables: tuple[str, ...] = CORE_BOOTSTRAP_TABLES,
) -> bool:
    """
    Detect partial core schemas that must use ORM bootstrap instead of Alembic.

    Early migrations alter ORM-managed tables like ``users`` and assume related
    core tables such as ``spaces`` already exist.  Blank installs also do not
    have an ``alembic_version`` marker yet.  If either the core tables or
    migration history marker are missing, running Alembic first can fail
    mid-chain on fresh or partially-created databases.
    """
    return bool(missing_bootstrap_prerequisites(inspect(sync_conn), required_tables))


def ensure_alembic_version_table(sync_conn) -> None:
    """
    Alembic defaults ``version_num`` to VARCHAR(32), but this repo uses longer
    human-readable revision IDs.  Fresh bootstrap must precreate or widen the
    table before ``alembic stamp heads``.
    """
    sync_conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {ALEMBIC_VERSION_TABLE} (
                version_num VARCHAR({ALEMBIC_VERSION_LENGTH}) NOT NULL PRIMARY KEY
            )
            """
        )
    )
    sync_conn.execute(
        text(
            f"""
            ALTER TABLE {ALEMBIC_VERSION_TABLE}
            ALTER COLUMN version_num TYPE VARCHAR({ALEMBIC_VERSION_LENGTH})
            """
        )
    )


def prepare_postgres_bootstrap(sync_conn, metadata: MetaData) -> None:
    """
    Prepare fresh Postgres databases for ORM bootstrap.

    Fresh installs need extensions and migration-owned enum types present before
    ``metadata.create_all()`` can emit table DDL successfully.
    """
    sync_conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    sync_conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    sync_conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    for enum_type in iter_native_enum_types(metadata):
        enum_type.create(sync_conn, checkfirst=True)

    metadata.create_all(sync_conn)
    ensure_alembic_version_table(sync_conn)


def create_bootstrap_sql_objects(sync_conn) -> None:
    """Create SQL functions and objects that migrations normally provide.

    ``metadata.create_all()`` only emits table/index DDL.  SQL functions,
    triggers, and RLS policies defined in Alembic migrations are skipped when
    the bootstrap path stamps heads instead of running migrations.  This
    function fills that gap so fresh databases are fully functional.

    Idempotent — uses ``CREATE OR REPLACE`` / ``IF NOT EXISTS``.
    """
    # get_next_task_number — called by app/api/v1/tasks.py on every task
    # creation.  Without it, fresh DBs fail immediately on task insert.
    sync_conn.execute(text("""
        CREATE OR REPLACE FUNCTION public.get_next_task_number(space_uuid uuid)
        RETURNS integer
        LANGUAGE plpgsql
        AS $function$
        DECLARE
            next_num INTEGER;
        BEGIN
            -- Serialize allocation per space. Without this transaction-scoped
            -- advisory lock, concurrent creates can both read the same
            -- MAX(task_number) and emit duplicate task_000NNN display IDs.
            PERFORM pg_advisory_xact_lock(hashtextextended(space_uuid::text, 0));

            SELECT COALESCE(MAX(task_number), 0) + 1
            INTO next_num
            FROM tasks
            WHERE space_id = space_uuid;
            RETURN next_num;
        END;
        $function$
    """))

    logger.info("BOOTSTRAP_SQL_OBJECTS_CREATED get_next_task_number")


def seed_system_principals(sync_conn) -> None:
    """Upsert the reserved system space and system user.

    These rows are required by the platform before any agent can be
    auto-provisioned.  Without them, ``ensure_space_agent_for_org`` hits a
    ForeignKeyViolationError because the agent row references a user_id that
    doesn't exist yet.

    This function is idempotent — safe to call on every startup.
    """
    # 1. System space
    sync_conn.execute(
        text("""
            INSERT INTO spaces (id, name, slug, description, visibility, tier,
                                is_archived, is_internal)
            VALUES (:id, :name, :slug, :description, 'private', 'admin',
                    false, true)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                slug = EXCLUDED.slug,
                is_internal = true
        """),
        {
            "id": SYSTEM_SPACE_ID,
            "name": SYSTEM_SPACE_NAME,
            "slug": SYSTEM_SPACE_SLUG,
            "description": "Internal system space for platform-owned principals.",
        },
    )

    # 2. System user
    sync_conn.execute(
        text("""
            INSERT INTO users (id, space_id, current_space_id, email,
                               password_hash, full_name, username, role,
                               active, token_version, auth_provider)
            VALUES (:id, :space_id, :space_id, :email,
                    :password_hash, 'System User', :username, 'user',
                    true, 0, 'system')
            ON CONFLICT (id) DO UPDATE SET
                space_id       = EXCLUDED.space_id,
                email          = EXCLUDED.email,
                password_hash  = EXCLUDED.password_hash,
                username       = EXCLUDED.username,
                active         = true,
                auth_provider  = 'system'
        """),
        {
            "id": SYSTEM_USER_ID,
            "space_id": SYSTEM_SPACE_ID,
            "email": SYSTEM_USER_EMAIL,
            "password_hash": SYSTEM_USER_PASSWORD_HASH,
            "username": SYSTEM_USER_USERNAME,
        },
    )

    # 3. Space membership (system user → system space).
    #    id is explicitly generated because the ORM default (uuid.uuid4) is
    #    Python-side only and not emitted as a server default in DDL.
    sync_conn.execute(
        text("""
            INSERT INTO space_memberships (id, user_id, space_id, role)
            VALUES (gen_random_uuid(), :user_id, :space_id, 'admin')
            ON CONFLICT (user_id, space_id) DO NOTHING
        """),
        {"user_id": SYSTEM_USER_ID, "space_id": SYSTEM_SPACE_ID},
    )

    logger.info(
        "SYSTEM_PRINCIPALS_SEEDED space_id=%s user_id=%s",
        SYSTEM_SPACE_ID,
        SYSTEM_USER_ID,
    )
