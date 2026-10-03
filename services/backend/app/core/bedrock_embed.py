"""
Bedrock embedding service — Amazon Titan Embed Text v2.

Used by the concierge router scorer (MCP server calls /api/v1/embed,
which delegates here). Owning all Bedrock embed calls in one place means
swapping the model later is a single-file change.

Model: amazon.titan-embed-text-v2:0
Dimensions: 512 (default, configurable: 256 | 512 | 1024)
Normalization: True (unit vectors — cosine similarity via dot product)

Usage:
    from app.core.bedrock_embed import titan_embed, titan_embed_batch

    vec = await titan_embed("JWT token expired")          # -> list[float], 512-dim
    vecs = await titan_embed_batch(["a", "b"])            # -> list[list[float]]
"""

import asyncio
import json
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_TITAN_MODEL_ID = os.getenv("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0")
_BEDROCK_REGION = os.getenv("AWS_DEFAULT_REGION", "us-west-2")
_DEFAULT_DIM = int(os.getenv("BEDROCK_EMBED_DIM", "512"))  # 256 | 512 | 1024

_client = None
_client_lock = threading.Lock()


def _get_bedrock_client():
    """Lazy singleton boto3 bedrock-runtime client."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        import boto3
        _client = boto3.client("bedrock-runtime", region_name=_BEDROCK_REGION)
        logger.info(f"Bedrock embed client initialised (region={_BEDROCK_REGION}, model={_TITAN_MODEL_ID})")
    return _client


async def titan_embed(
    text: str,
    dimensions: Optional[int] = None,
    normalize: bool = True,
) -> list[float]:
    """
    Embed a single text string via Titan Embed Text v2.
    Returns a normalized float vector (default 512-dim).
    Runs the blocking boto3 call in an executor to avoid blocking the event loop.
    """
    dim = dimensions or _DEFAULT_DIM
    client = _get_bedrock_client()

    body = json.dumps({
        "inputText": text,
        "dimensions": dim,
        "normalize": normalize,
    })

    loop = asyncio.get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: client.invoke_model(
            modelId=_TITAN_MODEL_ID,
            body=body,
            contentType="application/json",
            accept="application/json",
        ),
    )

    result = json.loads(response["body"].read())
    return result["embedding"]


async def titan_embed_batch(
    texts: list[str],
    dimensions: Optional[int] = None,
    normalize: bool = True,
) -> list[list[float]]:
    """
    Embed a batch of strings concurrently.
    Titan v2 has no native batch endpoint — we fan out concurrent calls.
    Respects Bedrock TPS limits; caller should implement retry on throttle.
    """
    if not texts:
        return []
    tasks = [titan_embed(t, dimensions=dimensions, normalize=normalize) for t in texts]
    return list(await asyncio.gather(*tasks))
