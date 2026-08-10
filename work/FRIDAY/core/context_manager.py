"""Conversation context manager — history, summarisation, branching.

Extracted from ``core/brain.py`` (WAVE2-ARCH refactor). ``ContextManager`` owns:

* ``history`` — the active conversation message list (``[{role, content}, ...]``)
* ``summarizer`` — a ``ConversationSummarizer`` instance
* ``_branches`` — forked conversation branches keyed by ``branch_id``
* ``_active_branch_id`` — the currently active branch (or ``None`` for main)

Backward compatibility
----------------------
``FridayBrain`` exposes ``conversation_history``, ``summarizer``, ``_branches``
and ``_active_branch_id`` as properties that delegate to its ``ContextManager``
instance, so existing tests and API routes that mutate these attributes
directly continue to work unchanged.
"""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("FridayBrain")


# ---------------------------------------------------------------------------
# Conversation summariser
# ---------------------------------------------------------------------------


class ConversationSummarizer:
    """Summarizes old conversation history to maintain context within token limits."""

    def __init__(self, brain_client=None, max_unsummarized: int = 20, summary_max_tokens: int = 500):
        self.brain_client = brain_client
        self.max_unsummarized = max_unsummarized
        self.summary_max_tokens = summary_max_tokens
        self.summaries: List[str] = []

    async def summarize_older_messages(self, messages: List[Dict]) -> List[Dict]:
        """Keep recent messages intact, summarize older ones into a condensed block."""
        if len(messages) <= self.max_unsummarized:
            return messages

        older = messages[:-self.max_unsummarized]
        recent = messages[-self.max_unsummarized:]

        # Build text to summarize
        older_text = "\n".join(
            f"{m.get('role', 'unknown')}: {m.get('content', '')}"
            for m in older
        )

        summary = await self._generate_summary(older_text)
        if summary:
            self.summaries.append(summary)

        return recent

    async def _generate_summary(self, text: str) -> Optional[str]:
        """Use the LLM to generate a conversation summary."""
        if not self.brain_client:
            # Heuristic fallback: just keep key facts
            return self._heuristic_summary(text)

        try:
            response = await self.brain_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=self.summary_max_tokens,
                messages=[{
                    "role": "user",
                    "content": (
                        "Summarize the key points, decisions, and facts from this "
                        "conversation excerpt in a concise paragraph:\n\n" + text
                    )
                }]
            )
            return response.content[0].text
        except Exception as e:
            logger.warning(f"Summary generation failed: {e}")
            return self._heuristic_summary(text)

    def _heuristic_summary(self, text: str) -> str:
        """Extract key sentences when LLM is unavailable."""
        sentences = text.split('.')
        keywords = [
            'important', 'decided', 'remember', 'prefer', 'need', 'want',
            'fact', 'name is', 'my name'
        ]
        key_sentences = [
            s.strip() for s in sentences
            if any(kw in s.lower() for kw in keywords)
        ]
        if key_sentences:
            return "Key points: " + "; ".join(key_sentences[:5])
        return ""

    def get_context_block(self) -> str:
        """Return all summaries as a context block for the system prompt."""
        if not self.summaries:
            return ""
        return (
            "\n\n## Previous Conversation Summaries\n"
            + "\n---\n".join(self.summaries)
        )


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class ContextManager:
    """Manages conversation history, summarisation, and branching."""

    def __init__(self, brain_client=None, max_unsummarized: int = 20):
        self.history: List[Dict[str, Any]] = []
        self.summarizer: ConversationSummarizer = ConversationSummarizer(
            brain_client=brain_client,
            max_unsummarized=max_unsummarized,
        )
        self._branches: Dict[str, Dict[str, Any]] = {}
        self._active_branch_id: Optional[str] = None
        # Snapshot of main conversation when switching into a branch
        self._main_history: Optional[List[Dict[str, Any]]] = None
        self._main_summaries: Optional[List[str]] = None

    # ------------------------------------------------------------------
    # Basic history operations
    # ------------------------------------------------------------------

    def append(self, role: str, content: Any) -> None:
        """Append a message to the active history."""
        self.history.append({"role": role, "content": content})

    def get_history(self) -> List[Dict[str, Any]]:
        """Return the active conversation history (live reference)."""
        return self.history

    def clear(self) -> None:
        """Clear conversation history and all summaries."""
        self.history = []
        self.summarizer.summaries = []

    # ------------------------------------------------------------------
    # Branching
    # ------------------------------------------------------------------

    async def branch(self, branch_point_index: int) -> str:
        """Fork the conversation at the given message index.

        Copies the history up to and including ``branch_point_index`` into
        a new branch and switches the active history to it. Returns the
        new ``branch_id``.
        """
        idx = max(0, min(branch_point_index, len(self.history) - 1))

        branch_history = list(self.history[: idx + 1])

        branch_id = f"branch_{_uuid.uuid4().hex[:8]}"
        self._branches[branch_id] = {
            "history": branch_history,
            "parent_message_id": str(branch_point_index),
            "created_at": datetime.now().isoformat(),
            "summaries": list(self.summarizer.summaries),
        }

        # Snapshot the main conversation before switching
        if self._active_branch_id is None:
            self._main_history = list(self.history)
            self._main_summaries = list(self.summarizer.summaries)

        # Switch to the new branch
        self._active_branch_id = branch_id
        self.history = branch_history
        self.summarizer.summaries = list(self._branches[branch_id]["summaries"])

        logger.info(
            "Created branch %s at message %d (%d messages copied)",
            branch_id, idx, len(branch_history),
        )
        return branch_id

    def switch_branch(self, branch_id: str) -> bool:
        """Switch the active conversation to a different branch.

        Returns ``True`` if the switch succeeded, ``False`` if the target
        branch does not exist.
        """
        if branch_id == "main":
            # Save current branch state
            if self._active_branch_id and self._active_branch_id in self._branches:
                self._branches[self._active_branch_id]["history"] = list(self.history)
                self._branches[self._active_branch_id]["summaries"] = list(
                    self.summarizer.summaries
                )
            self._active_branch_id = None
            # Restore main history snapshot if we have one
            if self._main_history is not None:
                self.history = list(self._main_history)
                self.summarizer.summaries = list(self._main_summaries or [])
            return True

        if branch_id not in self._branches:
            return False

        # Save current state
        if self._active_branch_id is None:
            self._main_history = list(self.history)
            self._main_summaries = list(self.summarizer.summaries)
        elif self._active_branch_id in self._branches:
            self._branches[self._active_branch_id]["history"] = list(self.history)
            self._branches[self._active_branch_id]["summaries"] = list(
                self.summarizer.summaries
            )

        # Switch to target branch
        self._active_branch_id = branch_id
        self.history = list(self._branches[branch_id]["history"])
        self.summarizer.summaries = list(
            self._branches[branch_id].get("summaries", [])
        )
        logger.info("Switched to branch %s", branch_id)
        return True

    def merge_branch_insight(self, branch_id: str, insight: str) -> bool:
        """Append a branch insight to the main conversation.

        Switches to the main branch, appends ``insight`` as a system
        message, and (if the caller was previously in ``branch_id``)
        switches back to that branch.

        Returns ``True`` if the insight was merged, ``False`` if the
        branch was not found.
        """
        if branch_id not in self._branches:
            return False

        was_in_branch = self._active_branch_id == branch_id
        if was_in_branch:
            self.switch_branch("main")
        elif self._active_branch_id is not None:
            # Save current branch, switch to main, append insight
            self.switch_branch("main")

        self.history.append({"role": "system", "content": insight})

        if was_in_branch:
            self.switch_branch(branch_id)
        return True

    def delete_branch(self, branch_id: str) -> bool:
        """Delete a conversation branch.

        Returns ``True`` if deleted, ``False`` if it does not exist or
        is the main branch.
        """
        if branch_id == "main" or branch_id not in self._branches:
            return False
        if self._active_branch_id == branch_id:
            self.switch_branch("main")
        del self._branches[branch_id]
        logger.info("Deleted branch %s", branch_id)
        return True

    def get_branches(self) -> List[Dict[str, Any]]:
        """Return a list of all branches with metadata, including main."""
        result: List[Dict[str, Any]] = []
        for bid, bdata in self._branches.items():
            result.append({
                "branch_id": bid,
                "parent_message_id": bdata["parent_message_id"],
                "created_at": bdata["created_at"],
                "message_count": len(bdata["history"]),
                "is_active": bid == self._active_branch_id,
            })
        # Include the main conversation as a synthetic branch entry.
        # Note: this preserves the (slightly quirky) original behaviour —
        # when inside a branch, ``main.message_count`` mirrors the active
        # branch's history length rather than the saved main snapshot.
        if self._active_branch_id is None:
            main_count = len(self.history)
        else:
            active_branch = self._branches.get(self._active_branch_id, {})
            main_count = len(active_branch.get("history", []))
        result.append({
            "branch_id": "main",
            "parent_message_id": None,
            "created_at": None,
            "message_count": main_count,
            "is_active": self._active_branch_id is None,
        })
        return result
