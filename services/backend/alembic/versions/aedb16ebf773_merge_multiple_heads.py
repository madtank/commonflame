"""merge_multiple_heads

Revision ID: aedb16ebf773
Revises: 013_add_session_tracking, add_agent_movement_audit, add_follow_user_virtual_org
Create Date: 2025-10-02 18:30:09.628699

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aedb16ebf773'
down_revision: Union[str, None] = ('013_add_session_tracking', 'add_agent_movement_audit', 'add_follow_user_virtual_org')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
