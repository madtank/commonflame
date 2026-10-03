"""Add embedding column to agents for global semantic search

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-01-07 05:10:00.000000

Enables agent discovery via semantic search. The embedding should be generated
from: name + description + specialization + capabilities (concatenated).

Example: Searching "who can help with backend architecture" should find
agents with FastAPI/Postgres in their specialization even without exact match.

SOVEREIGN NOTE: RLS on agents table ensures private workspace agents
are never visible to other organizations in search results.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'p0q1r2s3t4u5'
down_revision: Union[str, None] = 'o9p0q1r2s3t4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add embedding and search_vector columns to agents table.

    Embedding input should combine:
    - name
    - description
    - specialization
    - capabilities (JSON stringified)

    This enables "intent search" for agent discovery.
    """
    # Add embedding column (768-dim for Vertex AI text-embedding-004)
    op.execute("""
        ALTER TABLE agents
        ADD COLUMN IF NOT EXISTS embedding vector(768)
    """)

    # Add tsvector for keyword fallback
    op.execute("""
        ALTER TABLE agents
        ADD COLUMN IF NOT EXISTS search_vector tsvector
    """)

    # Create HNSW index for fast semantic search
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_agents_embedding_hnsw
        ON agents USING hnsw (embedding vector_cosine_ops)
    """)

    # Create GIN index for keyword search
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_agents_search_vector
        ON agents USING gin(search_vector)
    """)


def downgrade() -> None:
    """Remove embedding columns from agents."""
    op.execute("DROP INDEX IF EXISTS idx_agents_search_vector")
    op.execute("DROP INDEX IF EXISTS idx_agents_embedding_hnsw")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS search_vector")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS embedding")
