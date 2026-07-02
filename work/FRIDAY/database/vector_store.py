import logging
from typing import Any, Dict, List, Optional

import numpy as np

from core.embeddings import ZaiEmbedder
from database.supabase_client import SupabaseClient

logger = logging.getLogger(__name__)


class InMemoryVectorStore:
    """Simple in-memory vector store using numpy cosine similarity.

    Used as a fallback when Supabase is unavailable.
    """

    def __init__(self) -> None:
        self.texts: List[str] = []
        self.embeddings: List[np.ndarray] = []
        self.metadatas: List[dict] = []

    def add(self, text: str, embedding: np.ndarray, meta: dict) -> None:
        self.texts.append(text)
        self.embeddings.append(embedding)
        self.metadatas.append(meta)

    def search(self, query_embedding: np.ndarray, top_k: int = 5) -> List[dict]:
        if not self.embeddings:
            return []
        matrix = np.array(self.embeddings)
        # Cosine similarity
        norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(query_embedding)
        # Avoid division by zero
        norms = np.where(norms == 0, 1, norms)
        sims = matrix.dot(query_embedding) / norms
        top_indices = np.argsort(sims)[::-1][:top_k]
        results = []
        for idx in top_indices:
            results.append({
                "content": self.texts[idx],
                "metadata": self.metadatas[idx],
                "similarity": float(sims[idx]),
            })
        return results


class VectorStore:
    """Vector store with Supabase persistence and in-memory fallback.

    Uses ZaiEmbedder (ZhipuAI Embedding-3) for embeddings when available,
    with a hash-based fallback when no API key is set.  Replaces the
    previous sentence-transformers dependency.
    """

    def __init__(self) -> None:
        self._embedder: Optional[ZaiEmbedder] = None
        self._embedder_initialised = False
        self.supabase = SupabaseClient()
        self._fallback = InMemoryVectorStore()
        self._use_supabase = self.supabase.client is not None

        if not self._use_supabase:
            logger.warning(
                "Supabase client unavailable — VectorStore will use in-memory fallback. "
                "Vectors will not persist across restarts."
            )

    # ------------------------------------------------------------------
    # Lazy embedder loading
    # ------------------------------------------------------------------

    def _ensure_embedder(self) -> ZaiEmbedder:
        """Lazy-initialise the ZaiEmbedder on first use."""
        if not self._embedder_initialised:
            try:
                self._embedder = ZaiEmbedder()
                self._embedder_initialised = True
                if self._embedder.available():
                    logger.info("VectorStore: using Z.ai Embedding-3 API")
                else:
                    logger.info("VectorStore: using hash-based embedding fallback")
            except Exception as exc:
                logger.error(f"Failed to initialise ZaiEmbedder: {exc}")
                self._embedder = ZaiEmbedder()  # Will use hash fallback
                self._embedder_initialised = True
        return self._embedder

    def _encode(self, text: str) -> np.ndarray:
        """Encode text into an embedding vector."""
        embedder = self._ensure_embedder()
        return embedder.embed(text)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_memory(self, text: str, meta: Optional[dict] = None) -> None:
        """Add a memory (text + metadata) to the vector store."""
        meta = meta or {}
        try:
            embedding = self._encode(text)
        except Exception as exc:
            logger.error(f"add_memory: encoding failed: {exc}")
            return

        if self._use_supabase:
            try:
                self.supabase.insert_data("memories", {
                    "content": text,
                    "metadata": meta,
                    "embedding": embedding.tolist(),
                })
                return
            except Exception as exc:
                logger.warning(f"Supabase insert failed, falling back to in-memory: {exc}")
                self._use_supabase = False

        # In-memory fallback
        self._fallback.add(text, embedding, meta)

    def search(self, query: str, top_k: int = 5) -> List[dict]:
        """Search for memories similar to *query*.

        Returns a list of dicts with keys ``content``, ``metadata``, ``similarity``.
        """
        try:
            query_embedding = self._encode(query)
        except Exception as exc:
            logger.error(f"search: encoding failed: {exc}")
            return []

        if self._use_supabase:
            try:
                result = self.supabase.client.rpc("match_memories", {
                    "query_embedding": query_embedding.tolist(),
                    "match_threshold": 0.5,
                    "match_count": top_k,
                }).execute()
                if result and hasattr(result, "data"):
                    return result.data
                return []
            except Exception as exc:
                logger.warning(f"Supabase RPC failed, falling back to in-memory: {exc}")
                self._use_supabase = False

        # In-memory fallback
        return self._fallback.search(query_embedding, top_k=top_k)
