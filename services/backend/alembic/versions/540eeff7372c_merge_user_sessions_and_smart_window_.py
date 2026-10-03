"""merge_user_sessions_and_smart_window_heads

Revision ID: 540eeff7372c
Revises: 035_add_user_sessions, 7e35fa9982f1
Create Date: 2025-11-17 18:37:20.082330

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '540eeff7372c'
down_revision: Union[str, None] = ('035_add_user_sessions', '7e35fa9982f1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
