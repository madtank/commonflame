"""
Concierge Routing Log Model

Mirrors the schema created by migration router02_create_concierge_routing_logs.py.

Every routing decision from concierge_router.py lands here (via
POST /internal/routing-logs). The outcome_status column starts NULL and is
patched to its final value by the delivery layer via
PATCH /internal/routing-logs/{request_id}/outcome.

This is the Phase 2 training signal — don't let outcome_status stay NULL.
Owner: @logic_runner_677
"""
import uuid

from sqlalchemy import Boolean, Column, Float, Integer, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.dialects.postgresql import ENUM as PgEnum

from . import Base

# Mirrors the concierge_outcome_status PG enum created in router02 migration.
# create_type=False — the migration owns type creation; SQLAlchemy must not
# attempt DDL here (would break in environments where Alembic already ran).
OUTCOME_STATUS_VALUES = ("accepted", "redirected", "escalated", "no_response")

outcome_status_type = PgEnum(
    *OUTCOME_STATUS_VALUES,
    name="concierge_outcome_status",
    create_type=False,
)


class ConciergeRoutingLog(Base):
    __tablename__ = "concierge_routing_logs"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        nullable=False,
    )

    # Router-assigned request ID — idempotency key, unique per routing call
    request_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
        comment="UUID assigned by router at query time",
    )

    # Async outcome — written NULL at INSERT, patched by delivery layer later
    outcome_status = Column(
        outcome_status_type,
        nullable=True,
        comment=(
            "accepted=delivered | redirected=re-routed | "
            "escalated=sent to human | no_response=timed out | NULL=pending"
        ),
    )

    # Routing decision fields
    combined_winner = Column(
        String(64),
        nullable=True,
        comment="Handle of the winning agent (e.g. @logic_runner_677)",
    )
    resolution_step = Column(
        SmallInteger(),
        nullable=True,
        comment="Pipeline step: 1=mention 2=path 3=tag 4=score 5=tiebreak 6=fallback",
    )
    confidence = Column(
        Float(),
        nullable=True,
        comment="Top semantic similarity score (0.0–1.0). NULL for step 1/2.",
    )
    embedding_model = Column(
        String(128),
        nullable=True,
        comment="Model used for semantic scoring",
    )
    latency_ms = Column(
        Integer(),
        nullable=True,
        comment="End-to-end routing latency in milliseconds",
    )

    # Query fingerprint (non-PII — first 16 hex chars of SHA-256)
    query_hash = Column(
        String(16),
        nullable=False,
        comment="First 16 hex chars of SHA-256(query_text) for cache analysis",
    )

    # Pre-routing filter pipeline results (router03 migration)
    # Written at INSERT time — reflects the filter decision for this request.
    filter_stage = Column(
        String(),
        nullable=True,
        comment=(
            "Pass/flag result: passed | flagged_injection | flagged_spam | rate_limited. "
            "NULL = row predates router03 migration."
        ),
    )
    filter_reason = Column(
        String(),
        nullable=True,
        comment="Human-readable reason when filter_stage != 'passed'. NULL on clean passes.",
    )
    override_used = Column(
        Boolean(),
        nullable=True,
        comment="True if user sent override_token to bypass a filter flag. NULL on clean passes.",
    )

    # Rich outcome data for Phase 2 classifier training (complements outcome_status enum)
    # Written by PATCH /internal/routing-logs/{request_id}/outcome alongside outcome_status.
    # Stores: {correct_agent, human_corrected, follow_up_agent, correction_source, …}
    outcome_json = Column(
        JSONB(astext_type=String()),
        nullable=True,
        comment="Rich outcome metadata for Phase 2 training — flexible schema, evolves with training needs",
    )

    # Full structured pipeline data for debugging and threshold tuning
    pipeline_json = Column(
        JSONB(astext_type=String()),
        nullable=True,
        comment="Full tag-filter + semantic scoring pipeline data",
    )
    decision_json = Column(
        JSONB(astext_type=String()),
        nullable=True,
        comment="Full decision object: winner, resolution_step, tiebreak_used, scores",
    )

    # Pre-routing filter pipeline observability (added by migration router03)
    # Owner: @logic_runner_677 | Story: BE-ROUTER-FILTERS-01
    filter_stage = Column(
        String(32),
        nullable=True,
        comment=(
            "Pre-routing filter result: 'passed' | 'flagged_injection' | 'flagged_spam' | 'rate_limited'. "
            "NULL = row predates router03 migration."
        ),
    )
    filter_reason = Column(
        String(256),
        nullable=True,
        comment="Human-readable reason when filter_stage != 'passed'. NULL on clean passes.",
    )
    override_used = Column(
        Boolean(),
        nullable=True,
        comment="True if user sent an override_token to bypass a filter flag. NULL on clean passes.",
    )

    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=text("NOW()"),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<ConciergeRoutingLog(request_id={self.request_id}, "
            f"winner={self.combined_winner!r}, step={self.resolution_step}, "
            f"outcome={self.outcome_status!r})>"
        )
