"""merge_intelligence_and_relationships

Revision ID: 288aecb655e7
Revises: 41b2e2d93412, a1b2c3d4e5f6
Create Date: 2025-12-14 20:53:33.449757

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "288aecb655e7"
down_revision: Union[str, None] = ("41b2e2d93412", "a1b2c3d4e5f6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
