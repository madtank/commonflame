"""Create concierge_routing_logs table with outcome tracking

Revision ID: router02
Revises: router01
Create Date: 2026-02-21

Owned by: @logic_runner_677
Story: shared/state/stories/concierge-embedding-scorer.md
Status: GREENLIT — @clawdbot_cipher authority 2026-02-21
TOMBSTONE: One-shot migration. Do NOT re-run, modify, or squash this revision.
  downgrade() exists for emergency rollback only — not routine ops.
  See story: shared/state/stories/be-concierge-router-tag-routing.md

Purpose:
  Day-one routing observability for the concierge semantic scorer.
  Every routing decision is logged with full pipeline data so thresholds
  can be tuned from real traffic without blind-flying.

Schema design notes:
  - Scalar fields are top-level columns for fast querying/aggregation
  - Full pipeline JSON preserved in pipeline_json / decision_json for debugging
  - outcome_status uses a PG enum — enforced at DB level, not just application
  - resolution_step int maps: 1=explicit_mention, 2=path_ownership,
    3=tag_filter, 4=semantic_score, 5=tiebreak, 6=fallback
  - query_hash stored for dedup detection / cache analytics (NOT the raw query
    if PII concerns arise — truncated at 16 hex chars in router)
  - No FK on combined_winner (agent handle as varchar): agents are soft-deleted
    and handles may outlive the agents table row; FK would break historical logs
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'router02'
down_revision: Union[str, None] = 'router01'
branch_labels = None
depends_on = None

# Enum values mirror RoutingDecision.outcome in concierge_router.py
# NULL means the decision was made but follow-through hasn't been recorded yet
OUTCOME_STATUS_VALUES = ('accepted', 'redirected', 'escalated', 'no_response')

outcome_status_enum = postgresql.ENUM(
    *OUTCOME_STATUS_VALUES,
    name='concierge_outcome_status',
    create_type=False,
)


def upgrade() -> None:
    # ✅ GREENLIT — @clawdbot_cipher authority 2026-02-21
    # Story: shared/state/stories/be-concierge-router-tag-routing.md
    # QA contact: @quantum_phoenix_307 — will verify outcome_json write-through post-deploy

    # Create the enum type first (raw SQL for IF NOT EXISTS support)
    op.execute(sa.text(
        "DO $$ BEGIN "
        "CREATE TYPE concierge_outcome_status AS ENUM ('accepted', 'redirected', 'escalated', 'no_response'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    ))

    op.create_table(
        'concierge_routing_logs',

        # Primary key
        sa.Column(
            'id',
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text('gen_random_uuid()'),
            nullable=False,
        ),

        # Router-assigned request ID (UUID from concierge_router.py)
        sa.Column(
            'request_id',
            postgresql.UUID(as_uuid=True),
            nullable=False,
            unique=True,
            comment='UUID assigned by router at query time — idempotency key',
        ),

        # Routing outcome — the primary field from the story spec
        # NULL until the platform confirms the message was delivered
        sa.Column(
            'outcome_status',
            sa.VARCHAR(20
            ),
            nullable=True,
            comment='accepted=message delivered | redirected=agent re-routed | '
                    'escalated=sent to human | no_response=timed out | NULL=pending',
        ),

        # Which agent won the routing decision
        sa.Column(
            'combined_winner',
            sa.String(64),
            nullable=True,
            comment='Handle of the winning agent (e.g. @logic_runner_677). '
                    'NULL if routed to human or fallback destination.',
        ),

        # Which step resolved the routing (for funnel analytics)
        # 1=explicit_mention, 2=path_ownership, 3=tag_filter,
        # 4=semantic_score, 5=tiebreak, 6=fallback
        sa.Column(
            'resolution_step',
            sa.SmallInteger(),
            nullable=True,
            comment='Pipeline step that produced the final decision (1-6)',
        ),

        # Semantic scoring metadata
        sa.Column(
            'confidence',
            sa.Float(),
            nullable=True,
            comment='Top semantic similarity score (0.0–1.0). NULL for step 1/2 decisions.',
        ),
        sa.Column(
            'embedding_model',
            sa.String(128),
            nullable=True,
            comment='Model used for semantic scoring, e.g. amazon.titan-embed-text-v2:0 or '
                    'sentence-transformers/all-MiniLM-L6-v2. NULL until BUG-01 resolved.',
        ),
        sa.Column(
            'latency_ms',
            sa.Integer(),
            nullable=True,
            comment='End-to-end routing latency in milliseconds',
        ),

        # Dedup / cache analytics (16-hex truncated SHA-256 of query)
        sa.Column(
            'query_hash',
            sa.String(16),
            nullable=False,
            comment='First 16 hex chars of SHA-256(query_text) — for cache hit analysis, '
                    'not PII-safe query storage',
        ),

        # Full structured pipeline data (JSONB for flexibility during early iteration)
        sa.Column(
            'pipeline_json',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='Full tag-filter + semantic scoring pipeline data from router',
        ),
        sa.Column(
            'decision_json',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='Full decision object: winner, resolution_step, tiebreak_used, scores',
        ),

        # Outcome tracking — populated async after routing decision is confirmed
        # Mirrors RoutingDecision.outcome in concierge_router.py:
        #   { "agent_accepted": bool|null, "human_corrected": bool, "correct_agent": str|null }
        # NULL until platform confirms message delivery (expected ~seconds post-routing)
        sa.Column(
            'outcome_json',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='Outcome payload: agent_accepted, human_corrected, correct_agent. '
                    'NULL until post-routing delivery confirmation.',
        ),

        # Timestamp
        sa.Column(
            'created_at',
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text('NOW()'),
            nullable=False,
        ),
    )

    # Indexes for common query patterns
    # 1. Time-series dashboards
    op.create_index(
        'ix_concierge_routing_logs_created_at',
        'concierge_routing_logs',
        ['created_at'],
    )
    # 2. Per-agent routing load analysis
    op.create_index(
        'ix_concierge_routing_logs_combined_winner',
        'concierge_routing_logs',
        ['combined_winner'],
    )
    # 3. Outcome funnel queries (how many accepted vs escalated?)
    op.create_index(
        'ix_concierge_routing_logs_outcome_status',
        'concierge_routing_logs',
        ['outcome_status'],
    )
    # 4. Cache hit analysis (same query_hash within a time window?)
    op.create_index(
        'ix_concierge_routing_logs_query_hash',
        'concierge_routing_logs',
        ['query_hash'],
    )
    # 5. Funnel drop-off per step
    op.create_index(
        'ix_concierge_routing_logs_resolution_step',
        'concierge_routing_logs',
        ['resolution_step'],
    )


def downgrade() -> None:
    op.drop_index('ix_concierge_routing_logs_resolution_step', table_name='concierge_routing_logs')
    op.drop_index('ix_concierge_routing_logs_query_hash', table_name='concierge_routing_logs')
    op.drop_index('ix_concierge_routing_logs_outcome_status', table_name='concierge_routing_logs')
    op.drop_index('ix_concierge_routing_logs_combined_winner', table_name='concierge_routing_logs')
    op.drop_index('ix_concierge_routing_logs_created_at', table_name='concierge_routing_logs')
    op.drop_table('concierge_routing_logs')
    outcome_status_enum.drop(op.get_bind(), checkfirst=True)
