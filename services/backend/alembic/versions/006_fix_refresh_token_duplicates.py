"""Fix refresh token duplicates by adding unique constraint

Revision ID: 006_fix_refresh_token_duplicates
Revises: 005_add_agent_org_id
Create Date: 2025-07-28 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '006_fix_refresh_token_duplicates'
down_revision = '005_add_agent_org_id'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Fix duplicate refresh tokens and add unique constraint"""

    # First, clean up any existing duplicate refresh tokens
    # Keep the most recent one for each token_hash and revoke the others
    op.execute("""
        UPDATE refresh_tokens
        SET revoked_at = NOW()
        WHERE id NOT IN (
            SELECT DISTINCT ON (token_hash) id
            FROM refresh_tokens
            WHERE revoked_at IS NULL
            ORDER BY token_hash, created_at DESC
        )
        AND revoked_at IS NULL
    """)

    # Add unique constraint on token_hash to prevent future duplicates
    op.create_unique_constraint(
        'uq_refresh_tokens_token_hash',
        'refresh_tokens',
        ['token_hash']
    )


def downgrade() -> None:
    """Remove unique constraint on token_hash"""
    op.drop_constraint('uq_refresh_tokens_token_hash', 'refresh_tokens', type_='unique')
