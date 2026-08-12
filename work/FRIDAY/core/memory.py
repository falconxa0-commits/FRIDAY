import logging
import re
import importlib
from typing import List, Dict, Optional, Any
from datetime import datetime, timedelta, timezone

from core.embeddings import ZaiEmbedder

logger = logging.getLogger("FridayMemory")


class FridayMemory:
    """Advanced memory system with fact extraction, categorization, and decay."""

    def __init__(self, supabase_client=None, vector_store=None):
        """Initialize with optional dependency injection."""
        try:
            # Lazy import via importlib — Core must not statically depend on
            # the database layer. This preserves the layer boundary.
            SupabaseClient = importlib.import_module(
                "database.supabase_client"
            ).SupabaseClient
            VectorStore = importlib.import_module(
                "database.vector_store"
            ).VectorStore
            self.supabase = supabase_client or SupabaseClient()
            self.vector_store = vector_store or VectorStore()
        except Exception as e:
            logger.warning(
                f"Memory database init failed, using in-memory fallback: {e}"
            )
            self.supabase = None
            self.vector_store = None

        # In-memory fallback
        self._memories: List[Dict] = []
        self._session_facts: Dict[str, Any] = {}
        self._fact_patterns = self._build_fact_patterns()

    # ------------------------------------------------------------------
    # Fact extraction patterns
    # ------------------------------------------------------------------

    def _build_fact_patterns(self) -> List[Dict]:
        """Build regex patterns for fact extraction."""
        return [
            {
                "pattern": r"(?:my name is|i'm called|call me)\s+(\w+)",
                "category": "personal",
                "type": "name",
            },
            {
                "pattern": (
                    r"(?:i (?:live|am living|stay) in|i'm from)\s+"
                    r"(.+?)(?:\.|,|$)"
                ),
                "category": "personal",
                "type": "location",
            },
            {
                "pattern": (
                    r"(?:i (?:work|am working) (?:at|for|as))\s+"
                    r"(.+?)(?:\.|,|$)"
                ),
                "category": "work",
                "type": "job",
            },
            {
                "pattern": (
                    r"(?:i (?:like|love|enjoy|prefer|adore))\s+"
                    r"(.+?)(?:\.|,|$)"
                ),
                "category": "preference",
                "type": "likes",
            },
            {
                "pattern": (
                    r"(?:i (?:dislike|hate|don't like|can't stand))\s+"
                    r"(.+?)(?:\.|,|$)"
                ),
                "category": "preference",
                "type": "dislikes",
            },
            {
                "pattern": (
                    r"(?:my (?:favorite|fav) \w+ is)\s+(.+?)(?:\.|,|$)"
                ),
                "category": "preference",
                "type": "favorites",
            },
            {
                "pattern": (
                    r"(?:i (?:have|own|use))\s+(.+?)(?:\.|,|$)"
                ),
                "category": "personal",
                "type": "possessions",
            },
            {
                "pattern": (
                    r"(?:my (?:birthday|bday) is)\s+(.+?)(?:\.|,|$)"
                ),
                "category": "personal",
                "type": "birthday",
            },
            {
                "pattern": (
                    r"(?:i'm (?:allergic|allergic to))\s+(.+?)(?:\.|,|$)"
                ),
                "category": "health",
                "type": "allergy",
            },
            {
                "pattern": (
                    r"(?:i (?:need to|should|must|have to))\s+"
                    r"(.+?)(?:\.|,|$)"
                ),
                "category": "work",
                "type": "task",
            },
        ]

    # ------------------------------------------------------------------
    # Conversation storage
    # ------------------------------------------------------------------

    def store_conversation(
        self, role: str, content: str, metadata: Optional[Dict] = None
    ):
        """Store a conversation turn in both Supabase and vector store."""
        data = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {},
        }

        # Try database storage
        if self.supabase and self.supabase.client:
            try:
                self.supabase.insert_data("conversations", data)
            except Exception as e:
                logger.warning(
                    f"Failed to store conversation in Supabase: {e}"
                )

        # Try vector store
        if self.vector_store:
            try:
                self.vector_store.add_memory(content, data)
            except Exception as e:
                logger.warning(f"Failed to store in vector store: {e}")

        # Always keep in-memory
        self._memories.append(data)

        # Auto-extract facts from user messages
        if role == "user":
            try:
                self.extract_and_store_facts(content)
            except Exception as e:
                logger.debug(f"Non-critical error: {e}")

    # ------------------------------------------------------------------
    # Memory retrieval
    # ------------------------------------------------------------------

    def retrieve_relevant_memories(
        self, query: str, top_k: int = 5
    ) -> List[Dict]:
        """Retrieve semantically relevant memories."""
        # Try vector store first
        if self.vector_store:
            try:
                results = self.vector_store.search(query, top_k=top_k)
                if results:
                    return results
            except Exception as e:
                logger.warning(f"Vector search failed: {e}")

        # Fallback: keyword search in in-memory store
        query_words = set(query.lower().split())
        scored = []
        for mem in self._memories:
            content_words = set(mem.get("content", "").lower().split())
            overlap = len(query_words & content_words)
            if overlap > 0:
                scored.append((overlap, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [mem for _, mem in scored[:top_k]]

    # ------------------------------------------------------------------
    # Fact extraction
    # ------------------------------------------------------------------

    def extract_and_store_facts(
        self, text: str, category: Optional[str] = None
    ) -> List[str]:
        """Extract facts from text using pattern matching and store them."""
        text_lower = text.lower()
        facts_extracted = []

        for pattern_def in self._fact_patterns:
            matches = re.finditer(
                pattern_def["pattern"], text_lower, re.IGNORECASE
            )
            for match in matches:
                fact_value = match.group(1).strip()
                fact_key = f"{pattern_def['type']}:{fact_value}"

                # Store in session facts
                self._session_facts[fact_key] = {
                    "value": fact_value,
                    "category": category or pattern_def["category"],
                    "type": pattern_def["type"],
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                }

                # Store in vector store
                if self.vector_store:
                    try:
                        self.vector_store.add_memory(
                            f"{pattern_def['type']}: {fact_value}",
                            {
                                "category": category
                                or pattern_def["category"],
                                "type": pattern_def["type"],
                            },
                        )
                    except Exception as e:
                        logger.debug(f"Non-critical error: {e}")

                facts_extracted.append(
                    f"{pattern_def['type']}: {fact_value}"
                )

        # If explicit category provided, always store the full text
        if category:
            if self.vector_store:
                try:
                    self.vector_store.add_memory(
                        text, {"category": category}
                    )
                except Exception as e:
                    logger.debug(f"Non-critical error: {e}")
            self._session_facts[f"{category}:{text[:50]}"] = {
                "value": text,
                "category": category,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
            }

        if facts_extracted:
            logger.info(f"Extracted facts: {facts_extracted}")

        return facts_extracted

    # ------------------------------------------------------------------
    # User profile & recent conversations
    # ------------------------------------------------------------------

    def get_user_profile(self) -> Dict[str, Any]:
        """Compile all known facts about the user."""
        profile: Dict[str, Any] = {}
        for key, data in self._session_facts.items():
            cat = data.get("category", "unknown")
            if cat not in profile:
                profile[cat] = []
            profile[cat].append(data)
        return profile

    def get_recent_conversations(self, limit: int = 10) -> List[Dict]:
        """Get recent conversations from in-memory store."""
        return self._memories[-limit:]
