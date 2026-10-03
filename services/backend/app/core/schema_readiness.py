"""Database schema readiness checks for code paths that depend on recent migrations."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text


TASKS_TYPED_ASSIGNEE_REVISION = "ta01_typed_task_assignee"
TASKS_TYPED_ASSIGNEE_COLUMNS = (
    "assignee_type",
    "assignee_id",
    "assigned_by_type",
    "assigned_by_id",
)


@dataclass(frozen=True)
class SchemaReadiness:
    ready: bool
    revision: str
    missing_columns: tuple[str, ...] = ()
    applied_revisions: tuple[str, ...] = ()

    @property
    def detail(self) -> str:
        if self.ready:
            return "Task typed-assignee schema is ready"
        parts = [
            f"Database schema is behind code for task assignment; run alembic upgrade head and restart the API. Required revision: {self.revision}."
        ]
        if self.missing_columns:
            parts.append(f"Missing tasks columns: {', '.join(self.missing_columns)}.")
        if self.applied_revisions:
            parts.append(f"Current alembic revision(s): {', '.join(self.applied_revisions)}.")
        else:
            parts.append("No alembic revision marker found.")
        return " ".join(parts)

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "required_revision": self.revision,
            "missing_columns": list(self.missing_columns),
            "applied_revisions": list(self.applied_revisions),
            "detail": self.detail,
        }


async def check_tasks_typed_assignee_schema(db) -> SchemaReadiness:
    """Verify the DB has the typed task assignee migration needed by assignment writes."""

    column_names_sql = ", ".join(f"'{column_name}'" for column_name in TASKS_TYPED_ASSIGNEE_COLUMNS)
    column_rows = await db.execute(
        text(
            f"""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'tasks'
              AND column_name IN ({column_names_sql})
            """
        )
    )
    present_columns = {row[0] for row in column_rows.fetchall()}
    missing_columns = tuple(col for col in TASKS_TYPED_ASSIGNEE_COLUMNS if col not in present_columns)

    revision_rows = await db.execute(text("SELECT version_num FROM alembic_version"))
    applied_revisions = tuple(str(row[0]) for row in revision_rows.fetchall())
    # The live alembic_version table stores current heads, not full migration history.
    # A later descendant of ta01 will not equal ta01, so physical column presence is
    # the authoritative readiness signal; revisions are returned for diagnostics.
    return SchemaReadiness(
        ready=not missing_columns,
        revision=TASKS_TYPED_ASSIGNEE_REVISION,
        missing_columns=missing_columns,
        applied_revisions=applied_revisions,
    )
