"""Multi-modal memory — stores both text and visual memories.

Extends FridayMemory with methods to store visual context (screenshots,
images, diagrams) alongside text descriptions. Visual memories are
embedded using Z.ai Embedding-3 (or fallback to keyword search) and
can be retrieved by semantic query.

Examples:
    friday.store_visual("/tmp/screenshot.png", "Error in main.py line 42",
                        context={"app": "vscode"})
    friday.search_visual("error screen I saw last week")
    # → returns the screenshot with similarity score

When GLM-4V is available, the visual memory's text description is
enriched by GLM-4V's analysis of the image. When not available,
the caller-supplied description is used as-is.
"""
from __future__ import annotations

import base64
import datetime
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MultiModalMemory:
    """Stores and retrieves both text and visual memories.

    This is a standalone class (not a subclass of FridayMemory) so it
    can be composed in rather than requiring inheritance. Callers can
    use both classes side-by-side for text and visual memories.
    """

    def __init__(self, glm_brain=None, embedder=None):
        """
        Args:
            glm_brain: Optional GLMBrain instance for vision analysis.
                If None, tries to construct one (gracefully fails if no key).
            embedder: Optional ZaiEmbedder instance for semantic search.
                If None, tries to construct one (falls back to keyword search).
        """
        self._visual_memories: List[dict] = []
        self._glm_brain = glm_brain
        self._embedder = embedder

    # ------------------------------------------------------------------
    # Lazy initialisers
    # ------------------------------------------------------------------

    def _get_glm_brain(self):
        if self._glm_brain is None:
            try:
                from core.glm_brain import GLMBrain
                self._glm_brain = GLMBrain()
            except Exception as exc:
                logger.debug("GLMBrain not available: %s", exc)
                self._glm_brain = None
        return self._glm_brain

    def _get_embedder(self):
        if self._embedder is None:
            try:
                from core.embeddings import ZaiEmbedder
                self._embedder = ZaiEmbedder()
            except Exception as exc:
                logger.debug("ZaiEmbedder not available: %s", exc)
                self._embedder = None
        return self._embedder

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def store_visual(
        self,
        image_path: str,
        description: str,
        context: Optional[dict] = None,
    ) -> dict:
        """Store a visual memory.

        If GLM-4V is available, enriches the description with the
        vision model's analysis. Otherwise uses the caller-supplied
        description as-is.

        Args:
            image_path: Path to the image file on disk.
            description: A text description of what the image shows.
            context: Optional metadata (app, mode, tags, etc.)

        Returns:
            The stored memory dict (including the assigned id).
        """
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")

        ctx = context or {}
        rich_description = description

        # Try to enrich with GLM-4V
        glm = self._get_glm_brain()
        if glm and glm.available() and hasattr(glm, "vision_analyze"):
            try:
                with open(image_path, "rb") as f:
                    img_b64 = base64.b64encode(f.read()).decode("ascii")
                vision_desc = await glm.vision_analyze(
                    img_b64, "Describe this image in 2-3 sentences."
                )
                if vision_desc and vision_desc.strip():
                    rich_description = f"{description}\n\nGLM-4V analysis: {vision_desc.strip()}"
            except Exception as exc:
                logger.debug("GLM-4V enrichment failed: %s", exc)

        # Embed the description
        embedding: Optional[List[float]] = None
        emb = self._get_embedder()
        if emb:
            try:
                embedding = emb.embed(rich_description)
            except Exception as exc:
                logger.debug("Embedding failed: %s", exc)

        memory = {
            "id": f"vm_{len(self._visual_memories)}_{int(datetime.datetime.now().timestamp())}",
            "image_path": image_path,
            "description": description,
            "rich_description": rich_description,
            "embedding": embedding,
            "context": ctx,
            "timestamp": datetime.datetime.now().isoformat(),
        }
        self._visual_memories.append(memory)
        logger.info("Stored visual memory %s (%s)", memory["id"], image_path)
        return memory

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def search_visual(self, query: str, top_k: int = 5) -> List[dict]:
        """Semantic search across visual memories.

        If embeddings are available, uses cosine similarity. Otherwise
        falls back to keyword overlap scoring.

        Returns a list of dicts sorted by similarity (highest first):
            [{memory: <dict>, similarity: <float>}, ...]
        """
        if not self._visual_memories:
            return []

        # Try embedding-based search
        emb = self._get_embedder()
        if emb:
            try:
                query_embedding = emb.embed(query)
                results = []
                for mem in self._visual_memories:
                    if mem.get("embedding") is None:
                        continue
                    sim = _cosine_similarity(query_embedding, mem["embedding"])
                    results.append({"memory": mem, "similarity": sim})
                results.sort(key=lambda r: -r["similarity"])
                if results:
                    return results[:top_k]
            except Exception as exc:
                logger.debug("Embedding search failed: %s", exc)

        # Fallback: keyword overlap
        query_words = set(query.lower().split())
        results = []
        for mem in self._visual_memories:
            desc_words = set(mem["rich_description"].lower().split())
            overlap = len(query_words & desc_words)
            if overlap == 0:
                continue
            similarity = overlap / max(len(query_words), 1)
            results.append({"memory": mem, "similarity": similarity})
        results.sort(key=lambda r: -r["similarity"])
        return results[:top_k]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def list_visual_memories(self) -> List[dict]:
        """Return all visual memories (without embeddings)."""
        return [{k: v for k, v in m.items() if k != "embedding"}
                for m in self._visual_memories]

    def delete_visual_memory(self, memory_id: str) -> bool:
        """Delete a visual memory by id. Returns True if deleted."""
        original_len = len(self._visual_memories)
        self._visual_memories = [
            m for m in self._visual_memories if m["id"] != memory_id
        ]
        return len(self._visual_memories) < original_len

    def count(self) -> int:
        return len(self._visual_memories)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    import math
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
