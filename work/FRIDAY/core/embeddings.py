"""Z.ai Embeddings — ZhipuAI Embedding-3 API with hash-based fallback.

Provides text embedding via the ZhipuAI Embedding-3 model when a
GLM_API_KEY is available.  Falls back to a deterministic hash-based
embedding (for testing/offline use) when no key is present.
"""

import hashlib
import logging
import struct
from typing import List, Optional

import numpy as np

from config.settings import GLM_API_KEY

logger = logging.getLogger("ZaiEmbedder")

# ---------------------------------------------------------------------------
# Lazy import — mark as unavailable if zhipuai is not installed
# ---------------------------------------------------------------------------

_ZhipuAI = None
_ZHIPU_AVAILABLE = False

try:
    from zhipuai import ZhipuAI as _ZhipuClient  # noqa: N813
    _ZhipuAI = _ZhipuClient
    _ZHIPU_AVAILABLE = True
except ImportError:
    logger.info(
        "zhipuai package not installed. ZaiEmbedder will use hash-based fallback. "
        "Install with: pip install zhipuai"
    )
except Exception as exc:
    logger.warning(f"Unexpected error importing zhipuai: {exc}")


class ZaiEmbedder:
    """Text embedding using ZhipuAI Embedding-3 with hash fallback.

    When ``GLM_API_KEY`` is set and the ``zhipuai`` package is installed,
    embeddings are produced by the Embedding-3 API (1024-dimensional).

    When either is missing, a deterministic 256-dimensional hash-based
    embedding is used instead.  This is useful for testing and offline
    operation but has **no semantic meaning**.
    """

    EMBEDDING_DIM = 1024       # ZhipuAI Embedding-3 dimension
    FALLBACK_DIM = 256         # Hash-based fallback dimension

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or GLM_API_KEY
        self._client = None
        self._use_api = False

        if not _ZHIPU_AVAILABLE or not self.api_key:
            logger.info(
                "ZaiEmbedder: using hash-based fallback "
                "(zhipuai unavailable or no GLM_API_KEY)"
            )
            return

        self._use_api = True

    # ------------------------------------------------------------------
    # Lazy client construction (guarded by API key)
    # ------------------------------------------------------------------

    def _ensure_client(self):
        """Build the ZhipuAI client on first use, guarded by the API key."""
        if self._client is not None:
            return self._client

        if not self._use_api or not self.api_key:
            return None

        try:
            self._client = _ZhipuAI(api_key=self.api_key)
            logger.info("ZaiEmbedder: ZhipuAI client initialised")
            return self._client
        except Exception as exc:
            logger.error(f"ZaiEmbedder client construction failed: {exc}")
            self._use_api = False
            return None

    def available(self) -> bool:
        """Return True if API-based embeddings are available."""
        return self._use_api and self._ensure_client() is not None

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def embed(self, text: str) -> np.ndarray:
        """Generate an embedding vector for *text*.

        Uses the ZhipuAI Embedding-3 API when available, otherwise
        falls back to a deterministic hash-based vector.
        """
        if self._use_api:
            client = self._ensure_client()
            if client is not None:
                try:
                    response = client.embeddings.create(
                        model="Embedding-3",
                        input=text,
                    )
                    data = response.data[0] if response.data else None
                    if data and hasattr(data, "embedding"):
                        return np.array(data.embedding, dtype=np.float32)
                except Exception as exc:
                    logger.warning(f"ZhipuAI embedding failed, using fallback: {exc}")

        return self._hash_embed(text)

    def _hash_embed(self, text: str) -> np.ndarray:
        """Deterministic hash-based embedding for offline / no-key fallback.

        Produces a normalised float32 vector of ``FALLBACK_DIM`` dimensions.
        **Not semantically meaningful** — only useful for exact-match lookups.
        """
        vec = np.zeros(self.FALLBACK_DIM, dtype=np.float32)
        # Create multiple hashes to fill the vector
        for i in range(self.FALLBACK_DIM // 4):
            h = hashlib.sha256(f"{text}|chunk{i}".encode("utf-8")).digest()
            # Unpack 4 floats from the 32-byte hash
            for j in range(4):
                idx = i * 4 + j
                if idx < self.FALLBACK_DIM:
                    # Use 4 bytes as a float via struct
                    raw = h[j * 4 : (j + 1) * 4]
                    val = struct.unpack("f", raw)[0]
                    vec[idx] = val

        # Normalise to unit length
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    # ------------------------------------------------------------------
    # Similarity
    # ------------------------------------------------------------------

    @staticmethod
    def similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Compute cosine similarity between two embedding vectors."""
        n1 = np.linalg.norm(vec1)
        n2 = np.linalg.norm(vec2)
        if n1 == 0 or n2 == 0:
            return 0.0
        return float(np.dot(vec1, vec2) / (n1 * n2))
