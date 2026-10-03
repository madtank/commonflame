"""
Embedding model singleton for concierge router scoring.

Model: all-MiniLM-L6-v2 via fastembed (ONNX runtime — no PyTorch dependency)
Why fastembed over sentence-transformers:
  - ONNX runtime instead of PyTorch: ~200MB image impact vs ~2GB
  - Faster cold start on t3.medium
  - Same model quality for short-text semantic similarity
  - Production-tested (used by Qdrant at scale)

Usage:
    from app.core.embed import get_embedder, embed_text, embed_batch

    embedder = get_embedder()               # lazy-loaded singleton
    vec = embed_text("JWT token expired")   # -> list[float] (384-dim)
    vecs = embed_batch(["a", "b"])          # -> list[list[float]]
"""

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
# Persistent cache dir — survives container restarts when backed by a named volume.
# Falls back to /tmp if not set (dev/test environments without the volume mount).
_CACHE_DIR = os.getenv("FASTEMBED_CACHE_DIR", "/home/app/.fastembed_cache")
_embedder = None
_lock = threading.Lock()


def get_embedder():
    """
    Return the singleton fastembed TextEmbedding instance.
    Loads lazily on first call — model download happens once, cached locally.
    Thread-safe: uses a lock to prevent concurrent initialisation.
    """
    global _embedder
    if _embedder is not None:
        return _embedder

    with _lock:
        # Double-checked locking: re-check after acquiring lock
        if _embedder is not None:
            return _embedder

        try:
            from fastembed import TextEmbedding
            logger.info(f"Loading embedding model: {_MODEL_NAME} (cache: {_CACHE_DIR})")
            os.makedirs(_CACHE_DIR, exist_ok=True)
            _embedder = TextEmbedding(model_name=_MODEL_NAME, cache_dir=_CACHE_DIR)
            logger.info("Embedding model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise

    return _embedder


def embed_text(text: str) -> list[float]:
    """
    Embed a single text string.
    Returns a 384-dimensional float vector.
    """
    embedder = get_embedder()
    # fastembed returns a generator of numpy arrays
    result = list(embedder.embed([text]))
    return result[0].tolist()


def embed_batch(texts: list[str]) -> list[list[float]]:
    """
    Embed a batch of strings.
    Returns a list of 384-dimensional float vectors.
    More efficient than calling embed_text repeatedly.
    """
    if not texts:
        return []
    embedder = get_embedder()
    results = list(embedder.embed(texts))
    return [r.tolist() for r in results]
