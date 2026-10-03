"""reserve_user_keyword

Revision ID: 3f77e5a32f22
Revises: 540eeff7372c
Create Date: 2025-11-18 15:56:07.878071

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f77e5a32f22'
down_revision: Union[str, None] = '540eeff7372c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add CHECK constraint to prevent manual creation of agent named "user"
    # The "user" keyword is reserved for Pattern 1 transformations (/mcp/agents/user → {username})
    op.execute("""
        ALTER TABLE agents
        ADD CONSTRAINT no_user_keyword
        CHECK (LOWER(name) != 'user')
    """)


def downgrade() -> None:
    # Remove the constraint if we need to rollback
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS no_user_keyword")
