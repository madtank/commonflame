"""Add Bedrock AgentCore fields to agents + space_agent_id to organizations

Revision ID: ac01
Revises: router03
Create Date: 2026-03-07

Purpose:
  Adds support for Bedrock AgentCore-backed agents (origin='agentcore').
  - agents.bedrock_agent_id: Terraform-managed Bedrock Agent ID
  - agents.bedrock_agent_alias_id: Versioned alias for invocation
  - organizations.space_agent_id: Default agent for the space (no-mention routing target)

  These columns enable Mode A architecture: shared Bedrock Agent runtime
  with per-space/per-user session isolation via deterministic session_id.
"""
from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'ac01'
down_revision: Union[str, None] = 'router03'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Bedrock AgentCore fields on agents table
    op.add_column(
        'agents',
        sa.Column('bedrock_agent_id', sa.String(64), nullable=True,
                   comment='Bedrock Agent ID (from Terraform). NULL for non-AgentCore agents.'),
    )
    op.add_column(
        'agents',
        sa.Column('bedrock_agent_alias_id', sa.String(64), nullable=True,
                   comment='Bedrock Agent Alias ID for invocation (stable/canary). NULL for non-AgentCore agents.'),
    )

    # Space agent reference on organizations
    op.add_column(
        'organizations',
        sa.Column('space_agent_id', UUID(as_uuid=True), nullable=True,
                   comment='Default Space Agent for no-mention routing. References agents(id).'),
    )
    op.create_foreign_key(
        'fk_organizations_space_agent_id',
        'organizations', 'agents',
        ['space_agent_id'], ['id'],
        ondelete='SET NULL',
    )

    # Index for looking up agentcore agents by bedrock_agent_id
    op.create_index(
        'ix_agents_bedrock_agent_id',
        'agents',
        ['bedrock_agent_id'],
        postgresql_where=sa.text("bedrock_agent_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index('ix_agents_bedrock_agent_id', table_name='agents')
    op.drop_constraint('fk_organizations_space_agent_id', 'organizations', type_='foreignkey')
    op.drop_column('organizations', 'space_agent_id')
    op.drop_column('agents', 'bedrock_agent_alias_id')
    op.drop_column('agents', 'bedrock_agent_id')
