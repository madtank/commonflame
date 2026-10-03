#!/usr/bin/env python3
"""Repair narrowly-known Alembic state drift around the attachments/final merge path.

Auto-repairable cases:

1. Schema has the ``attachments`` table but not the ``attrls01`` RLS policy.
   In that case the uploads branch tip is missing from ``alembic_version`` and
   the safe repair is to insert ``upl01_attachments`` so normal
   ``alembic upgrade heads`` can walk the branch.

2. Schema has the ``attrls01`` attachment policy but Alembic bookkeeping is
   stranded on stale parent rows. The repair path can now either stamp the
   attachments branch merge head (legacy case) or, when the later ``rls02`` and
   final ``merge_heads_2026_04_11`` prerequisites are already satisfied in the
   physical schema, stamp all the way to the real repository head.

3. Schema is already at the final merge head but stale parent rows were left in
   ``alembic_version``. In that case the stale parents are deleted so
   ``alembic upgrade heads`` stops erroring with an overlap.

Anything ambiguous fails closed.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

UPLOAD_CHAIN_REVISIONS = (
    "dft01_expand_agent_mgmt_enums",
    "tk01_fix_task_number_function_space_id",
    "ua01_user_alerts",
    "upl01_attachments",
)
AUTH_HEAD_REVISION = "authspec02"
UPLOAD_HEAD_REVISION = "upl01_attachments"
ATTACHMENTS_MERGE_REVISION = "attrls01_attachments_rls"
RLS02_MERGE_REVISION = "rls02_agents_space_access"
TC04_REVISION = "tc04_message_id_idx"
FINAL_MERGE_REVISION = "merge_heads_2026_04_11"
TASK_QUEUE_REVISION = "tq02_queue_reminders"
TASK_TYPED_ASSIGNEE_REVISION = "ta01_typed_task_assignee"
OAUTH_AS_REVISION = "oauth_as01_persist_as_state"
OAUTH_AS_RECONCILE_REVISION = "oauth_as02_reconcile_as_tables"
OAUTH_AS_AGENT_BINDING_REVISION = "oauth_as03_refresh_agent_binding"
CONTEXT_CATALOG_REVISION = "ctxcat_v1_20260525"
TASK_LIFECYCLE_REVISION = "8e5c38a8d310"
AGENT_GROUPS_REVISION = "ag01_agent_groups"
LOCKDOWN_RELEASE_REVISION = "llr02_release_lockdown"
TASK_DISPLAY_ID_LOCK_REVISION = "td01_lock_task_number_allocation"
RLS_POLICY_NAME = "attachments_space_isolation"
ALLOWED_STALE_PARENT_REVISIONS = (AUTH_HEAD_REVISION, UPLOAD_HEAD_REVISION)
RLS02_STALE_PARENTS = (ATTACHMENTS_MERGE_REVISION, TC04_REVISION)
FINAL_MERGE_PARENT_REVISIONS = (
    RLS02_MERGE_REVISION,
    "7155801152f0",
    "ac01",
    "b7c8d9e0f1a2",
    "mrs01_create_message_read_status",
)
FINAL_STALE_PARENT_REVISIONS = (
    ATTACHMENTS_MERGE_REVISION,
    TC04_REVISION,
    *FINAL_MERGE_PARENT_REVISIONS,
)
POST_FINAL_CHAIN_REVISIONS = (
    TASK_QUEUE_REVISION,
    TASK_TYPED_ASSIGNEE_REVISION,
    OAUTH_AS_REVISION,
    OAUTH_AS_RECONCILE_REVISION,
    OAUTH_AS_AGENT_BINDING_REVISION,
    CONTEXT_CATALOG_REVISION,
)
POST_FINAL_STALE_PARENT_REVISIONS = (
    *FINAL_STALE_PARENT_REVISIONS,
    FINAL_MERGE_REVISION,
    *POST_FINAL_CHAIN_REVISIONS,
)
KNOWN_ATTACHMENT_TRACKING_REVISIONS = {
    *UPLOAD_CHAIN_REVISIONS,
    AUTH_HEAD_REVISION,
    ATTACHMENTS_MERGE_REVISION,
    RLS02_MERGE_REVISION,
    FINAL_MERGE_REVISION,
}

# Physical-schema checkpoints: (sentinel_table_created_by_revision, revision),
# ordered newest-first. A migration's DDL is applied atomically (transactional
# DDL), so the presence of its sentinel table proves that migration — and, since
# the chain is linear, all its ancestors — are physically present. The newest
# present sentinel is therefore the true physical frontier of the database.
#
# This is the generic, forward-looking self-heal for the recurring failure mode
# where the physical schema is current but ``alembic_version`` has drifted/become
# stranded (so ``alembic upgrade heads`` errors with an overlap). Extend this
# tuple when a future migration adds a recognizable table at a release point;
# that is far less brittle than the per-revision ladder below.
SCHEMA_CHECKPOINTS = (
    ("agent_groups", "ag01_agent_groups"),              # agent groups (group messaging)
    ("access_requests", "607c0ca18ecf"),                # invite-only waitlist gate (repo head)
    ("context_catalog_entries", "ctxcat_v1_20260525"),  # interactive context catalog
)

# Column sentinels: (table, column) -> revision, newest-first. For migrations
# that advance the head by ADDING COLUMNS (not a table), so the table-based
# checkpoints above can't represent them. Presence of the column proves the
# migration — and, since the chain is linear, all its ancestors — are physically
# applied. These sit NEWER than the table checkpoints, so they are checked first.
# Without an entry here, a column-only migration past the last table checkpoint
# makes the repair read its own alembic_version row as "ahead of frontier" and
# fail closed at boot (the agent-lifecycle c790a695f026 crash-loop, 2026-05-30).
COLUMN_CHECKPOINTS = (
    # users.last_login_at is created by llr01, but the checkpoint maps it to
    # the head llr02: llr02 is a DATA-ONLY migration (lockdown release) with no
    # schema change of its own, so its parent's column is the closest physical
    # sentinel. Trade-off: a crash between llr01 and llr02 followed by a drift
    # repair stamps llr02 without running its data UPDATE — acceptable because
    # llr02 is an idempotent fallback and the audited admin endpoint
    # POST /api/admin/access-requests/rollback-lockdown is the recovery path.
    (("users", "last_login_at"), LOCKDOWN_RELEASE_REVISION),
    (("tasks", "last_activity_at"), "8e5c38a8d310"),  # task lifecycle columns (#366)
    (("agents", "last_active_at"), "c790a695f026"),  # agent lifecycle columns
)

# Constraint sentinels: (table, constraint_name) -> revision, newest-first. Use
# these for migrations that advance the head by adding a constraint/index rather
# than a table or column. Presence of the named constraint proves the migration
# DDL finished; absence fails closed so boot can rerun the migration normally.
UNIQUE_CONSTRAINT_CHECKPOINTS = (
    (("tasks", "uq_tasks_space_task_number"), TASK_DISPLAY_ID_LOCK_REVISION),
)


def _physical_frontier_revision(inspector) -> str | None:
    """Return the newest checkpoint revision whose sentinel (column or table) is present."""
    table_names = set(inspector.get_table_names())

    def _column_present(table: str, column: str) -> bool:
        return table in table_names and column in {
            c["name"] for c in inspector.get_columns(table)
        }

    def _unique_constraint_present(table: str, constraint_name: str) -> bool:
        if table not in table_names:
            return False
        try:
            constraints = inspector.get_unique_constraints(table)
        except Exception:
            constraints = []
        if any(c.get("name") == constraint_name for c in constraints):
            return True

        # Some dialects reflect unique constraints as indexes. Treat the named
        # unique index as equivalent because Alembic/Postgres enforces the same
        # physical uniqueness gate through an underlying index.
        try:
            indexes = inspector.get_indexes(table)
        except Exception:
            indexes = []
        return any(
            idx.get("name") == constraint_name and bool(idx.get("unique"))
            for idx in indexes
        )

    for (table, constraint_name), revision in UNIQUE_CONSTRAINT_CHECKPOINTS:
        if _unique_constraint_present(table, constraint_name):
            return revision

    # users.last_login_at (llr01/llr02) is the newest checkpoint overall, so it
    # must win over the agent_groups table check below.
    if _column_present("users", "last_login_at"):
        return LOCKDOWN_RELEASE_REVISION

    # Agent groups is newer than the task-lifecycle column checkpoint. Check it
    # before the remaining column checkpoints so a successful rerun of ag01 does
    # not look like an unexpected ahead-of-frontier alembic row on the next boot.
    if "agent_groups" in table_names:
        return AGENT_GROUPS_REVISION

    for (table, column), revision in COLUMN_CHECKPOINTS:
        if _column_present(table, column):
            return revision
    for sentinel_table, revision in SCHEMA_CHECKPOINTS:
        if sentinel_table in table_names:
            return revision
    return None


def _frontier_ancestor_revisions(frontier: str) -> set[str]:
    """All revisions in the frontier's ancestry (including the frontier), per the
    Alembic script graph. Only rows proven to be ancestors of the frontier are
    safe to collapse; unknown or ahead-of-frontier rows must fail closed."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "alembic")
    )
    script = ScriptDirectory.from_config(cfg)
    return {frontier} | {rev.revision for rev in script.iterate_revisions(frontier, "base")}


@dataclass(frozen=True)
class RepairResult:
    action: str
    state: str
    detail: str = ""


def resolve_database_url() -> str:
    url = os.environ.get("MCP_DATABASE_URL")
    if url:
        return url

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL or MCP_DATABASE_URL is required")

    return url.replace("postgresql+asyncpg://", "postgresql://")


def _read_alembic_rows(conn) -> list[str]:
    return list(
        conn.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars()
    )


def _attachments_has_rls_policy(conn) -> bool:
    if conn.dialect.name != "postgresql":
        return False

    return bool(
        conn.execute(
            text(
                """
                SELECT 1
                FROM pg_policies
                WHERE schemaname = current_schema()
                  AND tablename = 'attachments'
                  AND policyname = :policy_name
                LIMIT 1
                """
            ),
            {"policy_name": RLS_POLICY_NAME},
        ).scalar()
    )


def _agents_policy_matches_rls02(conn) -> bool:
    if conn.dialect.name != "postgresql":
        return False

    policy_def = conn.execute(
        text(
            """
            SELECT pg_get_expr(polqual, polrelid)
            FROM pg_policy
            WHERE polrelid = 'agents'::regclass
              AND polname = 'agents_space_isolation'
            LIMIT 1
            """
        )
    ).scalar()
    if not policy_def:
        return False

    return "agent_space_access" in policy_def and "state = 'active'" in policy_def


def _apply_rls02_policy_sql(conn) -> None:
    conn.execute(text("DROP POLICY IF EXISTS agents_space_isolation ON agents"))
    conn.execute(
        text(
            """
            CREATE POLICY agents_space_isolation ON agents
            FOR ALL
            USING (
                current_setting('app.is_privileged', true) = 'true'
                OR space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid
                OR id IN (
                    SELECT agent_id FROM agent_space_access
                    WHERE space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid
                      AND state = 'active'
                )
            )
            WITH CHECK (
                current_setting('app.is_privileged', true) = 'true'
                OR space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid
                OR id IN (
                    SELECT agent_id FROM agent_space_access
                    WHERE space_id = NULLIF(current_setting('app.current_space_id', true), '')::uuid
                      AND state = 'active'
                )
            )
            """
        )
    )


def _insert_revision(conn, revision: str) -> None:
    conn.execute(
        text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
        {"revision": revision},
    )


def _delete_revisions(conn, revisions: list[str]) -> list[str]:
    deleted: list[str] = []
    for revision in revisions:
        rowcount = conn.execute(
            text("DELETE FROM alembic_version WHERE version_num = :revision"),
            {"revision": revision},
        ).rowcount or 0
        if rowcount:
            deleted.append(revision)
    return deleted


def _stamp_to(conn, revision: str, *, drop_revisions: list[str]) -> list[str]:
    deleted = _delete_revisions(conn, drop_revisions)
    current_revs = set(_read_alembic_rows(conn))
    if revision not in current_revs:
        _insert_revision(conn, revision)
    return deleted


def _unexpected_attachment_tracking_revisions(current_revs: list[str]) -> list[str]:
    return sorted(
        rev
        for rev in current_revs
        if rev in KNOWN_ATTACHMENT_TRACKING_REVISIONS
        and rev
        not in {
            AUTH_HEAD_REVISION,
            UPLOAD_HEAD_REVISION,
            ATTACHMENTS_MERGE_REVISION,
            RLS02_MERGE_REVISION,
            FINAL_MERGE_REVISION,
        }
    )


def _final_merge_parent_revisions_missing(current_revs: list[str]) -> list[str]:
    return [rev for rev in FINAL_MERGE_PARENT_REVISIONS[1:] if rev not in current_revs]


def _latest_post_final_revision(current_revs: list[str]) -> str | None:
    current = set(current_revs)
    for revision in reversed(POST_FINAL_CHAIN_REVISIONS):
        if revision in current:
            return revision
    return None


def repair_known_alembic_state(database_url: str) -> RepairResult:
    engine = create_engine(database_url)

    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            table_names = set(inspector.get_table_names())

            if "attachments" not in table_names:
                return RepairResult(action="noop", state="attachments_absent")

            if "alembic_version" not in table_names:
                return RepairResult(
                    action="error",
                    state="attachments_present_missing_alembic_version",
                    detail="attachments exists but alembic_version table is missing",
                )

            current_revs = _read_alembic_rows(conn)

            # Self-heal: physical schema current but alembic_version drifted.
            # If a checkpoint sentinel table exists, the true position is its
            # revision; collapse bookkeeping to that single revision so a normal
            # `alembic upgrade heads` applies only the genuinely-pending migrations.
            frontier = _physical_frontier_revision(inspector)
            if frontier is not None:
                if current_revs == [frontier]:
                    return RepairResult(
                        action="noop",
                        state="already_at_physical_frontier",
                        detail=f"revision={frontier}",
                    )
                # Fail closed: only collapse rows proven to be ancestors of the
                # frontier. An unknown or ahead-of-frontier row means the DB is in
                # an unexpected state — erasing it could lose tracking, so error.
                allowed = _frontier_ancestor_revisions(frontier)
                unexpected = sorted(rev for rev in current_revs if rev not in allowed)
                if unexpected:
                    # A failed/rolled-back agent-groups rollout can leave an
                    # alembic_version row for the direct child revision even
                    # though its sentinel table is absent. Drop only that known
                    # stranded child stamp so startup can run the migration
                    # normally; anything else still fails closed.
                    if (
                        frontier == TASK_LIFECYCLE_REVISION
                        and unexpected == [AGENT_GROUPS_REVISION]
                        and AGENT_GROUPS_REVISION in current_revs
                        and "agent_groups" not in table_names
                    ):
                        deleted = _delete_revisions(conn, [AGENT_GROUPS_REVISION])
                        return RepairResult(
                            action="repaired",
                            state="removed_stranded_child_revision",
                            detail=(
                                f"frontier={frontier} "
                                f"dropped_revisions={','.join(deleted)}"
                            ),
                        )
                    return RepairResult(
                        action="error",
                        state="physical_frontier_unexpected_revisions",
                        detail=(
                            f"frontier={frontier} "
                            f"unexpected_revisions={','.join(unexpected)}"
                        ),
                    )
                deleted = _delete_revisions(conn, [r for r in current_revs if r != frontier])
                if frontier not in current_revs:
                    _insert_revision(conn, frontier)
                return RepairResult(
                    action="repaired",
                    state="reconciled_to_physical_frontier",
                    detail=(
                        f"stamped_revision={frontier} "
                        f"dropped_revisions={','.join(deleted) if deleted else 'none'}"
                    ),
                )

            unexpected_revs = _unexpected_attachment_tracking_revisions(current_revs)
            if unexpected_revs:
                return RepairResult(
                    action="error",
                    state="attachments_present_ambiguous_tracking",
                    detail=(
                        "unexpected_tracked_revisions=" + ",".join(unexpected_revs)
                    ),
                )

            latest_post_final = _latest_post_final_revision(current_revs)
            if latest_post_final:
                allowed_revisions = set(POST_FINAL_STALE_PARENT_REVISIONS)
                unexpected_post_final = sorted(
                    rev for rev in current_revs if rev not in allowed_revisions
                )
                if unexpected_post_final:
                    return RepairResult(
                        action="error",
                        state="post_final_ambiguous_tracking",
                        detail="unexpected_revisions=" + ",".join(unexpected_post_final),
                    )

                stale_post_final = [
                    rev
                    for rev in POST_FINAL_STALE_PARENT_REVISIONS
                    if rev in current_revs and rev != latest_post_final
                ]
                if stale_post_final:
                    deleted = _delete_revisions(conn, stale_post_final)
                    return RepairResult(
                        action="repaired",
                        state="post_final_stale_parents_cleaned",
                        detail=(
                            f"kept_revision={latest_post_final} "
                            f"dropped_revisions={','.join(deleted)}"
                        ),
                    )

                return RepairResult(
                    action="noop",
                    state="already_at_post_final_head",
                    detail=f"revision={latest_post_final}",
                )

            if FINAL_MERGE_REVISION in current_revs:
                stale_final = [rev for rev in FINAL_STALE_PARENT_REVISIONS if rev in current_revs]
                unexpected_final = sorted(
                    rev
                    for rev in current_revs
                    if rev != FINAL_MERGE_REVISION and rev not in FINAL_STALE_PARENT_REVISIONS
                )
                if unexpected_final:
                    return RepairResult(
                        action="error",
                        state="final_merge_ambiguous_tracking",
                        detail="unexpected_revisions=" + ",".join(unexpected_final),
                    )
                if stale_final:
                    deleted = _delete_revisions(conn, stale_final)
                    return RepairResult(
                        action="repaired",
                        state="final_merge_stale_parents_cleaned",
                        detail=f"dropped_revisions={','.join(deleted)}",
                    )
                return RepairResult(action="noop", state="already_at_final_merge_head")

            if RLS02_MERGE_REVISION in current_revs:
                stale_rls02 = [p for p in RLS02_STALE_PARENTS if p in current_revs]
                if stale_rls02:
                    deleted = _delete_revisions(conn, stale_rls02)
                    return RepairResult(
                        action="repaired",
                        state="rls02_stale_parents_cleaned",
                        detail=f"dropped_revisions={','.join(deleted)}",
                    )

                missing_merge_parents = _final_merge_parent_revisions_missing(current_revs)
                if not missing_merge_parents:
                    deleted = _stamp_to(
                        conn,
                        FINAL_MERGE_REVISION,
                        drop_revisions=list(FINAL_STALE_PARENT_REVISIONS),
                    )
                    return RepairResult(
                        action="repaired",
                        state="advanced_from_rls02_to_final_merge_head",
                        detail=(
                            f"stamped_revision={FINAL_MERGE_REVISION} "
                            f"dropped_revisions={','.join(deleted) if deleted else 'none'}"
                        ),
                    )

            schema_has_attrls01_rls = _attachments_has_rls_policy(conn)
            if schema_has_attrls01_rls:
                missing_merge_parents = _final_merge_parent_revisions_missing(current_revs)
                tc04_present = TC04_REVISION in current_revs
                can_advance_to_final_head = tc04_present and not missing_merge_parents

                if can_advance_to_final_head:
                    if not _agents_policy_matches_rls02(conn):
                        _apply_rls02_policy_sql(conn)
                    deleted = _stamp_to(
                        conn,
                        FINAL_MERGE_REVISION,
                        drop_revisions=list(FINAL_STALE_PARENT_REVISIONS),
                    )
                    return RepairResult(
                        action="repaired",
                        state="schema_advanced_to_final_merge_head",
                        detail=(
                            f"stamped_revision={FINAL_MERGE_REVISION} "
                            f"dropped_revisions={','.join(deleted) if deleted else 'none'}"
                        ),
                    )

                deleted = _stamp_to(
                    conn,
                    ATTACHMENTS_MERGE_REVISION,
                    drop_revisions=list(ALLOWED_STALE_PARENT_REVISIONS),
                )
                return RepairResult(
                    action="repaired",
                    state="schema_at_attachments_merge_head",
                    detail=(
                        f"stamped_revision={ATTACHMENTS_MERGE_REVISION} "
                        f"dropped_revisions={','.join(deleted) if deleted else 'none'}"
                    ),
                )

            if UPLOAD_HEAD_REVISION in current_revs:
                return RepairResult(action="noop", state="upload_branch_tip_already_recorded")

            _insert_revision(conn, UPLOAD_HEAD_REVISION)
            return RepairResult(
                action="repaired",
                state="missing_upload_branch_tip",
                detail=f"inserted_revision={UPLOAD_HEAD_REVISION}",
            )
    finally:
        engine.dispose()


def main() -> int:
    try:
        result = repair_known_alembic_state(resolve_database_url())
    except (RuntimeError, SQLAlchemyError) as exc:
        print(f"MIGRATION_REPAIR_ERROR state=unhandled detail={exc}", file=sys.stderr)
        return 1

    if result.action == "repaired":
        print(f"MIGRATION_REPAIR_APPLIED state={result.state} {result.detail}".strip())
        return 0

    if result.action == "noop":
        suffix = f" detail={result.detail}" if result.detail else ""
        print(f"MIGRATION_REPAIR_NOOP state={result.state}{suffix}")
        return 0

    print(f"MIGRATION_REPAIR_ERROR state={result.state} detail={result.detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
