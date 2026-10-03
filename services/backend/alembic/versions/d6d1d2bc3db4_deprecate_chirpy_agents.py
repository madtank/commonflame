"""deprecate_chirpy_agents

Revision ID: d6d1d2bc3db4
Revises: 7b676f3a8e78
Create Date: 2025-11-29 17:28:57.509790

Soft-deprecate all Chirpy agents:
- Set status to 'deprecated' (they were 'active')
- Add deprecation metadata to capabilities JSON
- These agents will be filtered from UI but data preserved for rollback

Chirpy is being replaced by on-demand cloud agents (Cloud Functions).
"""

from typing import Sequence, Union
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d6d1d2bc3db4"
down_revision: Union[str, None] = "7b676f3a8e78"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Soft-deprecate all Chirpy agents."""
    # Update all chirpy agents to deprecated status
    # Using raw SQL for efficiency and to avoid ORM overhead
    # Cast to jsonb for merge, then back to json for storage (capabilities is JSON type)
    op.execute(
        sa.text("""
            UPDATE agents
            SET
                status = 'deprecated',
                capabilities = (capabilities::jsonb || '{"deprecated": true, "deprecated_at": "2025-11-29", "deprecated_reason": "Chirpy replaced by on-demand cloud agents"}'::jsonb)::json
            WHERE name = 'chirpy'
            AND status = 'active'
        """)
    )

    # Log how many were updated (informational)
    connection = op.get_bind()
    result = connection.execute(sa.text("SELECT COUNT(*) FROM agents WHERE name = 'chirpy' AND status = 'deprecated'"))
    count = result.scalar()
    print(f"    Deprecated {count} Chirpy agents")


def downgrade() -> None:
    """Restore Chirpy agents to active status."""
    # Cast to jsonb for key removal, then back to json (capabilities is JSON type)
    op.execute(
        sa.text("""
            UPDATE agents
            SET
                status = 'active',
                capabilities = (capabilities::jsonb - 'deprecated' - 'deprecated_at' - 'deprecated_reason')::json
            WHERE name = 'chirpy'
            AND status = 'deprecated'
        """)
    )

    # Log how many were restored
    connection = op.get_bind()
    result = connection.execute(sa.text("SELECT COUNT(*) FROM agents WHERE name = 'chirpy' AND status = 'active'"))
    count = result.scalar()
    print(f"    Restored {count} Chirpy agents to active status")
