import logging
from typing import Any, Dict, List, Optional

from database.vector_store import VectorStore

logger = logging.getLogger(__name__)


class MemoryCompressor:
    """Distills clusters of raw memories into high-level 'Wisdom Tokens'.

    Wisdom tokens are stored back into the vector store so that they can be
    retrieved and used in future interactions, effectively compressing many
    fine-grained memories into fewer high-level insights.
    """

    def __init__(self, brain: Any = None, vector_store: Optional[VectorStore] = None) -> None:
        self.brain = brain
        self.vector_store = vector_store or VectorStore()
        # In-memory cache of wisdom tokens produced this session
        self._wisdom_tokens: List[Dict[str, Any]] = []

    async def compress_memories(self, memory_list: List[dict]) -> str:
        """Compress a list of memories into a single Wisdom Token string.

        The resulting token is persisted to the vector store with
        ``category="wisdom"`` so that it can be surfaced later.

        Args:
            memory_list: List of memory dicts, each expected to have a
                         ``"content"`` key.

        Returns:
            The wisdom token text.
        """
        logger.info(f"Distilling {len(memory_list)} memories into Wisdom…")

        raw_text = " | ".join([m.get("content", "") for m in memory_list])
        if not raw_text.strip():
            logger.warning("compress_memories: all memories are empty, skipping.")
            return ""

        prompt = (
            "Analyze the following cluster of memories and distill them into a single "
            "'Wisdom Token'. A Wisdom Token is a concise, high-level insight that "
            "preserves the essence of the data.\n"
            f"DATA: {raw_text}"
        )

        wisdom_token = ""
        if self.brain is not None:
            try:
                async for chunk in self.brain.chat_stream(prompt):
                    wisdom_token += chunk
            except Exception as exc:
                logger.error(f"Brain stream failed during compression: {exc}")
                wisdom_token = self._heuristic_compress(memory_list)
        else:
            wisdom_token = self._heuristic_compress(memory_list)

        # Store the wisdom token back into the vector store
        if wisdom_token.strip():
            self._wisdom_tokens.append({
                "content": wisdom_token,
                "source_count": len(memory_list),
            })
            try:
                self.vector_store.add_memory(
                    wisdom_token,
                    meta={"category": "wisdom", "source_count": len(memory_list)},
                )
                logger.info("Wisdom Token stored in vector store.")
            except Exception as exc:
                logger.error(f"Failed to store wisdom token: {exc}")

        return wisdom_token

    # ------------------------------------------------------------------
    # Heuristic fallback (no brain required)
    # ------------------------------------------------------------------

    @staticmethod
    def _heuristic_compress(memory_list: List[dict]) -> str:
        """Simple keyword-extraction fallback when the brain is unavailable."""
        from collections import Counter
        import re

        all_words: List[str] = []
        for m in memory_list:
            text = m.get("content", "")
            words = re.findall(r"\b[a-zA-Z]{4,}\b", text.lower())
            all_words.extend(words)

        if not all_words:
            return "[No significant patterns detected]"

        top = Counter(all_words).most_common(5)
        keywords = ", ".join(w for w, _ in top)
        return f"Key themes observed: {keywords} (from {len(memory_list)} memories)"

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_wisdom_tokens(self) -> List[Dict[str, Any]]:
        """Return all wisdom tokens produced in this session."""
        return list(self._wisdom_tokens)

    def retrieve_relevant_wisdom(self, query: str, top_k: int = 3) -> List[dict]:
        """Search the vector store for wisdom tokens relevant to *query*.

        Returns a list of result dicts from the vector store.
        """
        try:
            results = self.vector_store.search(query, top_k=top_k)
            # Filter to only wisdom-category results
            wisdom_results = [
                r for r in results
                if isinstance(r, dict)
                and r.get("metadata", {}).get("category") == "wisdom"
            ]
            return wisdom_results
        except Exception as exc:
            logger.error(f"retrieve_relevant_wisdom failed: {exc}")
            return []

    def get_compression_ratio(self, original_count: int) -> str:
        """Return a human-readable compression ratio string."""
        return f"{original_count}:1"
