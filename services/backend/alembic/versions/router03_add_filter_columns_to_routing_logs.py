"""Add filter_stage and filter_reason columns to concierge_routing_logs

Revision ID: router03
Revises: router02
Create Date: 2026-02-21

Owned by: @logic_runner_677
Story: shared/state/stories/be-router-filter-pipeline.md
Status: GREENLIT — @clawdbot_cipher authority 2026-02-21
TOMBSTONE: One-shot migration. Do NOT re-run, modify, or squash this revision.
  downgrade() exists for emergency rollback only — not routine ops.

Purpose:
  Adds observability columns for the pre-routing filter pipeline
  (BE-ROUTER-FILTERS-01). Every filter decision — pass or flag — is
  now recorded with stage and reason, enabling:
    - Audit trail for injection/spam flags and user overrides
    - False positive analysis (filter_stage=flagged but override_used=true)
    - Production threshold tuning from real traffic

Schema notes:
  - filter_stage: nullable text — populated on every routing request.
      Values: 'passed' | 'flagged_injection' | 'flagged_spam' | 'rate_limited'
      NULL = pre-filter (router03 not yet deployed for this row).
      Using text, not enum — filter stage vocabulary may evolve; nullable allows
      backward compat with existing rows created before router03.
  - filter_reason: nullable text — human-readable reason when flagged.
      NULL on passed messages (intentional — no reason to write on clean pass).
  - override_used: nullable bool — True when user sent override_token to bypass flag.
      NULL on clean passes. Enables "override rate" metric without extra query.

These are write-only from the router layer; read by audit queries and
future Phase 2 ML threshold analysis.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = 'router03'
down_revision: Union[str, None] = 'router02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ✅ GREENLIT — @clawdbot_cipher authority 2026-02-21
    # Story: shared/state/stories/be-router-filter-pipeline.md

    op.add_column(
        'concierge_routing_logs',
        sa.Column('filter_stage', sa.Text(), nullable=True, comment=(
            "Pass/flag result of pre-routing filter pipeline. "
            "Values: passed | flagged_injection | flagged_spam | rate_limited. "
            "NULL = row predates router03 migration."
        )),
    )

    op.add_column(
        'concierge_routing_logs',
        sa.Column('filter_reason', sa.Text(), nullable=True, comment=(
            "Human-readable reason when filter_stage != 'passed'. "
            "NULL on clean passes."
        )),
    )

    op.add_column(
        'concierge_routing_logs',
        sa.Column('override_used', sa.Boolean(), nullable=True, comment=(
            "True if user sent an override_token to bypass a filter flag. "
            "NULL on clean passes (no override needed)."
        )),
    )

    # Index on filter_stage for audit queries: "show me all flagged injection attempts"
    op.create_index(
        'ix_concierge_routing_logs_filter_stage',
        'concierge_routing_logs',
        ['filter_stage'],
        postgresql_where=sa.text("filter_stage IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index('ix_concierge_routing_logs_filter_stage', table_name='concierge_routing_logs')
    op.drop_column('concierge_routing_logs', 'override_used')
    op.drop_column('concierge_routing_logs', 'filter_reason')
    op.drop_column('concierge_routing_logs', 'filter_stage')
