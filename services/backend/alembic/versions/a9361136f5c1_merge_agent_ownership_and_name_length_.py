"""merge_agent_ownership_and_name_length_fixes

Revision ID: a9361136f5c1
Revises: 012_remove_agent_org_id, 015_increase_agent_name_length_only
Create Date: 2025-08-05 17:15:52.672392

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9361136f5c1'
down_revision: Union[str, None] = ('012_remove_agent_org_id', '015_increase_agent_name_length_only')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
