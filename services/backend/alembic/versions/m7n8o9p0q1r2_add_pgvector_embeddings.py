"""Add pgvector embeddings for semantic search

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-01-07 04:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'm7n8o9p0q1r2'
down_revision: Union[str, None] = 'l6m7n8o9p0q1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add embedding columns and indexes for semantic search.

    Uses pgvector extension with 768 dimensions for GCP Vertex AI
    text-embedding-004 compatibility (GCP-native, data stays in VPC).

    Adds both:
    - vector embeddings for semantic similarity search
    - tsvector for fast keyword/full-text search (hybrid approach)
    """
    # Ensure pgvector extension is enabled (supported on Cloud SQL PostgreSQL)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Add embedding columns using pgvector's native vector type
    # 768 dimensions for Vertex AI text-embedding-004
    op.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS embedding vector(768)")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS embedding vector(768)")

    # Create HNSW indexes for fast approximate nearest neighbor search
    # HNSW provides better query performance than IVFFlat at our scale
    # Note: Indexes only apply to non-null values, so this is safe even
    # before embeddings are populated
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_embedding_hnsw
        ON tasks USING hnsw (embedding vector_cosine_ops)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_messages_embedding_hnsw
        ON messages USING hnsw (embedding vector_cosine_ops)
    """)

    # Add tsvector columns for fast keyword search (hybrid approach)
    op.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS search_vector tsvector")
    op.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS search_vector tsvector")

    # Create GIN indexes for tsvector full-text search
    op.execute("CREATE INDEX IF NOT EXISTS idx_tasks_search_vector ON tasks USING gin(search_vector)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_messages_search_vector ON messages USING gin(search_vector)")


def downgrade() -> None:
    """Remove embedding columns and indexes."""
    # Drop indexes first
    op.execute("DROP INDEX IF EXISTS idx_messages_search_vector")
    op.execute("DROP INDEX IF EXISTS idx_tasks_search_vector")
    op.execute("DROP INDEX IF EXISTS idx_messages_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_tasks_embedding_hnsw")

    # Drop columns
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS search_vector")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS search_vector")
    op.execute("ALTER TABLE messages DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS embedding")
