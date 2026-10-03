"""
⚠️  DEAD CODE — DO NOT REGISTER OR WIRE

This file was drafted during BUG-01 planning as a Titan v2 wrapper at /api/v1/embed.
It was superseded before implementation by the existing /internal/embed endpoint,
which uses all-MiniLM-L6-v2 via fastembed (app/core/embed.py).

Canonical embed path:
  POST /internal/embed  →  app/api/v1/internal.py  →  app/core/embed.py (MiniLM)

This file is NOT registered in api/main.py. It has no effect.
Archive or delete when next touching this area.

Archived: 2026-02-21 by @logic_runner_677 (BUG-01 consolidation)
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...core.bedrock_embed import titan_embed, titan_embed_batch
from ...core.rls import SecureSession, get_secure_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["embed"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class EmbedRequest(BaseModel):
    text: Optional[str] = Field(None, description="Single string to embed.")
    texts: Optional[list[str]] = Field(None, description="Batch of strings to embed.")
    dimensions: Optional[int] = Field(
        None,
        description="Output dimensionality: 256, 512 (default), or 1024.",
        ge=256,
        le=1024,
    )
    normalize: bool = Field(True, description="Normalize to unit vectors (recommended for cosine similarity).")


class EmbedResponse(BaseModel):
    embedding: Optional[list[float]] = Field(None, description="Vector for single-text requests.")
    embeddings: Optional[list[list[float]]] = Field(None, description="Vectors for batch requests.")
    model: str = Field(..., description="Model identifier.")
    dim: int = Field(..., description="Vector dimensionality.")


# ---------------------------------------------------------------------------
# POST /api/v1/embed
# ---------------------------------------------------------------------------

@router.post(
    "/embed",
    response_model=EmbedResponse,
    summary="Embed text via Amazon Titan Embed Text v2 (Bedrock)",
)
async def embed(
    body: EmbedRequest,
    session: SecureSession = Depends(get_secure_session),
):
    """
    Embed one string or a batch using Amazon Titan Embed Text v2.

    - **text**: single string → `embedding` in response
    - **texts**: list of strings → `embeddings` in response
    - **dimensions**: 256 | 512 (default) | 1024
    - **normalize**: default true — recommended for cosine-similarity scoring

    Requires a valid Bearer token (agent or user JWT).
    """
    if body.text is None and not body.texts:
        raise HTTPException(status_code=422, detail="Provide 'text' (single) or 'texts' (batch).")
    if body.text is not None and body.texts is not None:
        raise HTTPException(status_code=422, detail="Provide either 'text' or 'texts', not both.")

    try:
        if body.text is not None:
            vec = await titan_embed(body.text, dimensions=body.dimensions, normalize=body.normalize)
            return EmbedResponse(
                embedding=vec,
                model="amazon.titan-embed-text-v2:0",
                dim=len(vec),
            )
        else:
            vecs = await titan_embed_batch(body.texts, dimensions=body.dimensions, normalize=body.normalize)
            dim = len(vecs[0]) if vecs else (body.dimensions or 512)
            return EmbedResponse(
                embeddings=vecs,
                model="amazon.titan-embed-text-v2:0",
                dim=dim,
            )
    except Exception as e:
        logger.error(f"Embed error: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Embedding service error: {str(e)}")
