"""merge notifications and cloud agents

Revision ID: 70eb6b63f37a
Revises: 006_create_mentions, 021_add_org_tier
Create Date: 2025-11-22 17:12:36.993120

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '70eb6b63f37a'
down_revision: Union[str, None] = ('006_create_mentions', '021_add_org_tier')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
