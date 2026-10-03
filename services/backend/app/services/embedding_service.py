"""
Embedding Service for Semantic Search

Provides vector embeddings for tasks, messages, and agents.
Supports multiple providers with automatic fallback.

SOVEREIGN WARNING: Embeddings from different providers are NOT compatible.
If you switch providers, existing embeddings must be regenerated.

Providers:
- ollama: Local development (nomic-embed-text, 768-dim)
- vertex: Production GCP (text-embedding-004, 768-dim)

Usage:
    from app.services.embedding_service import embedding_service

    # Generate embedding for text
    vector = await embedding_service.embed("search query")

    # Generate embeddings for batch
    vectors = await embedding_service.embed_batch(["text1", "text2"])
"""

import os
import logging
import httpx
from typing import List, Optional
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# Configuration
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "ollama")  # ollama | vertex
EMBEDDING_DIMENSIONS = 768  # Must match DB schema


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @abstractmethod
    async def embed(self, text: str) -> List[float]:
        """Generate embedding for a single text."""
        pass

    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for multiple texts."""
        pass


class OllamaProvider(EmbeddingProvider):
    """
    Local embedding provider using Ollama.

    Model: nomic-embed-text (768 dimensions)
    Use for: Local development, offline testing
    """

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = base_url
        self.model = "nomic-embed-text"
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def embed(self, text: str) -> List[float]:
        """Generate embedding using Ollama."""
        client = await self._get_client()

        try:
            response = await client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text}
            )
            response.raise_for_status()
            data = response.json()

            embedding = data.get("embedding", [])
            if len(embedding) != EMBEDDING_DIMENSIONS:
                logger.warning(
                    f"Unexpected embedding dimensions: {len(embedding)} (expected {EMBEDDING_DIMENSIONS})"
                )

            return embedding

        except Exception as e:
            logger.error(f"Ollama embedding failed: {e}")
            raise

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for batch (sequential for Ollama)."""
        # Ollama doesn't have native batch support, so we process sequentially
        embeddings = []
        for text in texts:
            embedding = await self.embed(text)
            embeddings.append(embedding)
        return embeddings

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


class VertexAIProvider(EmbeddingProvider):
    """
    Production embedding provider using Google Vertex AI.

    Model: text-embedding-004 (768 dimensions)
    Use for: Production, GCP-native deployment

    Requires:
    - GOOGLE_APPLICATION_CREDENTIALS or GCP workload identity
    - VERTEX_PROJECT_ID environment variable
    - VERTEX_LOCATION environment variable (default: us-central1)
    """

    def __init__(self):
        self.project_id = os.getenv("VERTEX_PROJECT_ID")
        self.location = os.getenv("VERTEX_LOCATION", "us-central1")
        self.model = "text-embedding-004"
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            # In production, use google-auth for credentials
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def _get_access_token(self) -> str:
        """Get GCP access token for API calls."""
        try:
            # Try to get token from metadata server (Cloud Run)
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
                    headers={"Metadata-Flavor": "Google"},
                    timeout=5.0
                )
                if response.status_code == 200:
                    return response.json()["access_token"]
        except Exception:
            pass

        # Fallback: use gcloud CLI token (local dev with gcloud auth)
        import subprocess
        try:
            result = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass

        raise RuntimeError("Could not obtain GCP access token")

    async def embed(self, text: str) -> List[float]:
        """Generate embedding using Vertex AI."""
        embeddings = await self.embed_batch([text])
        return embeddings[0] if embeddings else []

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings using Vertex AI batch API."""
        if not self.project_id:
            raise RuntimeError("VERTEX_PROJECT_ID not set")

        client = await self._get_client()
        token = await self._get_access_token()

        url = (
            f"https://{self.location}-aiplatform.googleapis.com/v1/"
            f"projects/{self.project_id}/locations/{self.location}/"
            f"publishers/google/models/{self.model}:predict"
        )

        # Vertex AI batch format
        instances = [{"content": text} for text in texts]

        try:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"instances": instances}
            )
            response.raise_for_status()
            data = response.json()

            embeddings = []
            for prediction in data.get("predictions", []):
                embedding = prediction.get("embeddings", {}).get("values", [])
                embeddings.append(embedding)

            return embeddings

        except Exception as e:
            logger.error(f"Vertex AI embedding failed: {e}")
            raise

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


class EmbeddingService:
    """
    Main embedding service with provider abstraction.

    Automatically selects provider based on EMBEDDING_PROVIDER env var.
    Provides fallback to keyword search if embedding fails.
    """

    def __init__(self):
        self._provider: Optional[EmbeddingProvider] = None
        self._provider_name = EMBEDDING_PROVIDER

    def _get_provider(self) -> EmbeddingProvider:
        if self._provider is None:
            if self._provider_name == "vertex":
                self._provider = VertexAIProvider()
                logger.info("Using Vertex AI embedding provider (production)")
            else:
                self._provider = OllamaProvider()
                logger.info("Using Ollama embedding provider (local)")
        return self._provider

    async def embed(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding for text.

        Returns None if embedding fails (allows fallback to keyword search).
        """
        if not text or not text.strip():
            return None

        try:
            provider = self._get_provider()
            return await provider.embed(text.strip())
        except Exception as e:
            logger.warning(f"Embedding failed, falling back to keyword search: {e}")
            return None

    async def embed_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        Generate embeddings for multiple texts.

        Returns None for any text that fails.
        """
        # Filter empty texts
        valid_texts = [(i, t.strip()) for i, t in enumerate(texts) if t and t.strip()]

        if not valid_texts:
            return [None] * len(texts)

        try:
            provider = self._get_provider()
            embeddings = await provider.embed_batch([t for _, t in valid_texts])

            # Map back to original indices
            result = [None] * len(texts)
            for (orig_idx, _), embedding in zip(valid_texts, embeddings):
                result[orig_idx] = embedding

            return result

        except Exception as e:
            logger.warning(f"Batch embedding failed: {e}")
            return [None] * len(texts)

    async def close(self):
        """Close provider connections."""
        if self._provider:
            await self._provider.close()
            self._provider = None

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def dimensions(self) -> int:
        return EMBEDDING_DIMENSIONS


# Global singleton
embedding_service = EmbeddingService()
