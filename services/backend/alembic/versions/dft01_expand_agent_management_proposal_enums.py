"""Expand agent management proposal constraints for canonical draft lifecycle.

Revision ID: dft01_expand_agent_mgmt_enums
Revises: sec02_agent_mgmt
Create Date: 2026-03-19 02:35:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "dft01_expand_agent_mgmt_enums"
down_revision = "sec02_agent_mgmt"
branch_labels = None
depends_on = None


_OLD_PROPOSAL_TYPES = """
    'create_user_agent', 'update_user_agent', 'disable_user_agent_global',
    'create_space_agent', 'update_space_agent',
    'attach_user_agent_to_space', 'move_user_agent_between_spaces'
"""

_NEW_PROPOSAL_TYPES = """
    'agents.create.sandbox', 'agents.create.privileged', 'agents.create.with_credentials',
    'spaces.create.personal', 'spaces.create.team', 'spaces.create.community',
    'spaces.join.community', 'spaces.join.protected'
"""

_OLD_PROPOSAL_STATUSES = """
    'pending', 'partially_approved', 'approved', 'rejected',
    'cancelled', 'expired', 'executed'
"""

_NEW_PROPOSAL_STATUSES = """
    'under_review', 'executing', 'failed'
"""


def _recreate_constraints(*, include_canonical_values: bool) -> None:
    proposal_types = _OLD_PROPOSAL_TYPES
    proposal_statuses = _OLD_PROPOSAL_STATUSES
    if include_canonical_values:
        proposal_types = f"{proposal_types}, {_NEW_PROPOSAL_TYPES}"
        proposal_statuses = f"{proposal_statuses}, {_NEW_PROPOSAL_STATUSES}"

    op.execute(
        """
        ALTER TABLE agent_management_proposals
        DROP CONSTRAINT IF EXISTS ck_proposal_type
        """
    )
    op.execute(
        f"""
        ALTER TABLE agent_management_proposals ADD CONSTRAINT ck_proposal_type
        CHECK (proposal_type IN ({proposal_types}))
        """
    )

    op.execute(
        """
        ALTER TABLE agent_management_proposals
        DROP CONSTRAINT IF EXISTS ck_proposal_status
        """
    )
    op.execute(
        f"""
        ALTER TABLE agent_management_proposals ADD CONSTRAINT ck_proposal_status
        CHECK (status IN ({proposal_statuses}))
        """
    )


def upgrade() -> None:
    _recreate_constraints(include_canonical_values=True)


def downgrade() -> None:
    _recreate_constraints(include_canonical_values=False)
