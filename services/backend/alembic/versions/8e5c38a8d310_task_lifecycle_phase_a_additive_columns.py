"""task_lifecycle_phase_a_additive_columns

Phase A of task-lifecycle controls (Option A — reuse/extend queue_state, no
separate lifecycle_state). Purely additive: adds nullable satellite columns the
new queue_state values (archived/superseded/deferred/stale/retained) need. No
data backfill here — last_activity_at is populated by a separate post-deploy
script so this migration stays metadata-only/fast and won't risk the ECS boot
waiter. Existing lifecycle-adjacent fields are reused, not duplicated:
snoozed_until (=defer_until), deadline (=due_at), stale_at/stale_reason,
blocked_by_ids, links, reminder_* subsystem, task_notes (activity + audit).

Design: docs/plans/2026-05-30-task-lifecycle-backend-design.md

Revision ID: 8e5c38a8d310
Revises: c790a695f026
Create Date: 2026-05-31 00:03:22.887327

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '8e5c38a8d310'
down_revision: Union[str, None] = 'c790a695f026'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (name, column factory) — all nullable, all additive. NOTE: superseded_by_task_id
# is added WITHOUT an inline ForeignKey on purpose — see upgrade() / _SUPERSEDED_FK.
_COLUMNS = [
    ("superseded_by_task_id", lambda: sa.Column("superseded_by_task_id", UUID(as_uuid=True), nullable=True)),
    ("retain_reason", lambda: sa.Column("retain_reason", sa.Text(), nullable=True)),
    ("review_at", lambda: sa.Column("review_at", sa.DateTime(timezone=True), nullable=True)),
    ("archived_at", lambda: sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)),
    ("archived_by", lambda: sa.Column("archived_by", UUID(as_uuid=True), nullable=True)),
    ("archived_reason", lambda: sa.Column("archived_reason", sa.String(120), nullable=True)),
    ("lifecycle_reason", lambda: sa.Column("lifecycle_reason", sa.String(120), nullable=True)),
    ("lifecycle_changed_at", lambda: sa.Column("lifecycle_changed_at", sa.DateTime(timezone=True), nullable=True)),
    ("lifecycle_changed_by", lambda: sa.Column("lifecycle_changed_by", UUID(as_uuid=True), nullable=True)),
    ("last_activity_at", lambda: sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True)),
]

_SUPERSEDED_FK = "fk_tasks_superseded_by_task_id"


def _existing_columns() -> set:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {c["name"] for c in inspector.get_columns("tasks")}


def _existing_fks() -> set:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return {fk["name"] for fk in inspector.get_foreign_keys("tasks")}


def upgrade() -> None:
    existing = _existing_columns()
    for name, factory in _COLUMNS:
        if name not in existing:
            op.add_column("tasks", factory())

    # Add the self-FK on superseded_by_task_id as a SEPARATE, NOT VALID constraint.
    # An inline ForeignKey on add_column makes PG emit ADD CONSTRAINT that validates
    # against existing rows and takes a lock on the (large) tasks table — that would
    # break the "metadata-only/fast" guarantee and risk the ECS startup-migration
    # path. NOT VALID skips the scan; the column is brand-new (all NULL) so there is
    # nothing to validate anyway. Optional VALIDATE CONSTRAINT can run later (Phase C).
    if _SUPERSEDED_FK not in _existing_fks():
        op.create_foreign_key(
            _SUPERSEDED_FK,
            "tasks", "tasks",
            ["superseded_by_task_id"], ["id"],
            ondelete="SET NULL",
            postgresql_not_valid=True,
        )


def downgrade() -> None:
    existing_fks = _existing_fks()
    if _SUPERSEDED_FK in existing_fks:
        op.drop_constraint(_SUPERSEDED_FK, "tasks", type_="foreignkey")
    existing = _existing_columns()
    for name, _ in reversed(_COLUMNS):
        if name in existing:
            op.drop_column("tasks", name)
