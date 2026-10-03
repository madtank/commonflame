"""add case-insensitive agent name unique index

Revision ID: 019_case_insensitive_agent_name
Revises: acf709d5ab30
Create Date: 2025-11-06 00:00:00.000000

Changes:
1. Add unique index on LOWER(name) for case-insensitive global uniqueness
2. This prevents agents like "Agent", "agent", "AGENT" from being created
3. Matches the API validation which already checks case-insensitively

Background:
- API currently enforces global case-insensitive uniqueness (agents.py:555)
- Database constraint is (user_id, name) which is case-sensitive
- This migration adds an additional index for case-insensitive enforcement
- Keeps existing (user_id, name) constraint for data integrity

Example conflicts prevented:
- User A creates "chirpy" → User B cannot create "Chirpy" or "CHIRPY"
- User A creates "test_agent" → Same user cannot create "Test_Agent"
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '019_case_insensitive_agent_name'
down_revision = 'acf709d5ab30'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add case-insensitive unique index on agent names (except cloud agents)

    This creates a partial functional unique index on LOWER(name) to enforce
    case-insensitive uniqueness globally across all users, EXCEPT for cloud agents
    like 'chirpy' which can be shared across users.
    """
    # Create partial unique index on LOWER(name) for case-insensitive global uniqueness
    # Excludes cloud agents (chirpy, etc.) which can be shared across users
    op.execute("""
        CREATE UNIQUE INDEX idx_agents_name_lower_unique
        ON agents (LOWER(name))
        WHERE LOWER(name) NOT IN ('chirpy');
    """)


def downgrade() -> None:
    """
    Remove case-insensitive unique index

    WARNING: Downgrading will allow case variations of agent names again.
    This could lead to confusion and security issues.
    """
    op.drop_index('idx_agents_name_lower_unique', table_name='agents')
