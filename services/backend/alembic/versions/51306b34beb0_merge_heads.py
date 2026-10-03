"""merge heads

Revision ID: 51306b34beb0
Revises: 022_add_is_archived, d6d1d2bc3db4
Create Date: 2025-12-07 07:25:05.854425

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "51306b34beb0"
down_revision: Union[str, None] = ("022_add_is_archived", "d6d1d2bc3db4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
