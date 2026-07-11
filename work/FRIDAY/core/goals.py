"""Goal tracking system — Friday tracks your goals and nudges progress.

Supports learning, project, habit, and financial goals.
Generates real nudges based on actual goal data and progress history.
"""
import asyncio
import datetime
import logging
import uuid
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class GoalTracker:
    """Tracks personal goals and generates progress nudges."""

    def __init__(self):
        self._goals: Dict[str, dict] = {}

    async def set_goal(self, description: str, target_date: str, type: str = "project",
                       metadata: Optional[dict] = None) -> dict:
        """Set a new goal."""
        goal_id = f"goal_{uuid.uuid4().hex[:8]}"
        goal = {
            "id": goal_id,
            "description": description,
            "target_date": target_date,
            "type": type,
            "metadata": metadata or {},
            "created_at": datetime.datetime.now().isoformat(),
            "progress_updates": [],
            "status": "active",
        }
        self._goals[goal_id] = goal
        logger.info(f"Goal set: {goal_id} — {description}")
        return goal

    async def update_progress(self, goal_id: str, progress_note: str) -> dict:
        """Log progress on a goal."""
        goal = self._goals.get(goal_id)
        if not goal:
            raise ValueError(f"Goal {goal_id} not found")
        entry = {
            "note": progress_note,
            "timestamp": datetime.datetime.now().isoformat(),
        }
        goal["progress_updates"].append(entry)
        return entry

    async def get_nudge(self, goal_id: str) -> str:
        """Generate a relevant nudge based on actual goal data."""
        goal = self._goals.get(goal_id)
        if not goal:
            return "Goal not found."

        updates = goal["progress_updates"]
        streak = self._compute_streak(goal)
        days_left = self._days_until(goal["target_date"])

        if days_left is not None and days_left < 0:
            return f"Goal '{goal['description']}' is overdue by {abs(days_left)} days. Consider revising or making progress today."
        elif days_left is not None and days_left <= 3:
            return f"Only {days_left} days left for '{goal['description']}'. Focus on critical tasks."
        elif streak == 0:
            return f"You haven't logged progress on '{goal['description']}' recently. Even a small step keeps momentum."
        elif streak >= 7:
            return f"Great {streak}-day streak on '{goal['description']}'! What's next?"
        else:
            return f"Goal '{goal['description']}' has {len(updates)} updates. What can you do today?"

    async def get_streak(self, goal_id: str) -> dict:
        """Get the current streak for a goal."""
        goal = self._goals.get(goal_id)
        if not goal:
            return {"goal_id": goal_id, "streak": 0}
        return {"goal_id": goal_id, "streak": self._compute_streak(goal)}

    def get_all_goals(self) -> List[dict]:
        """Return all active goals."""
        return list(self._goals.values())

    def get_todays_progress(self) -> List[dict]:
        """Return progress updates from today."""
        today = datetime.date.today().isoformat()
        results = []
        for goal in self._goals.values():
            todays = [u for u in goal["progress_updates"]
                      if u.get("timestamp", "").startswith(today)]
            if todays:
                results.append({"goal_id": goal["id"], "description": goal["description"],
                                "updates_today": len(todays)})
        return results

    def _compute_streak(self, goal: dict) -> int:
        dates = set()
        for u in goal["progress_updates"]:
            try:
                d = datetime.datetime.fromisoformat(u["timestamp"]).date()
                dates.add(d)
            except (ValueError, KeyError):
                continue
        if not dates:
            return 0
        today = datetime.date.today()
        streak = 0
        d = today
        while d in dates:
            streak += 1
            d -= datetime.timedelta(days=1)
        return streak

    def _days_until(self, target_date: str) -> Optional[int]:
        try:
            target = datetime.date.fromisoformat(target_date[:10])
            return (target - datetime.date.today()).days
        except (ValueError, TypeError):
            return None

    async def detect_progress_evidence(self, goal_id: str) -> list:
        """Look for real evidence of progress in actual behavior."""
        goal = self._goals.get(goal_id)
        if not goal:
            return []

        description = goal.get("description", "").lower()
        goal_type = goal.get("type", "project")
        evidence = []

        # Scan research logs for goal-related queries
        try:
            from api.routes.stats import _request_log
            import datetime
            today = datetime.date.today().isoformat()
            today_logs = [r for r in _request_log if r.get("timestamp", "").startswith(today)]
            # Check if any logs reference the goal description keywords
            desc_words = [w for w in description.split() if len(w) > 3]
            for log in today_logs:
                # We can't see the actual query, but we can check the model used
                # This is a heuristic — real implementation would scan brain memory
                pass
        except Exception:
            pass

        # Scan memory for goal-related facts
        try:
            from core.memory import FridayMemory
            mem = FridayMemory()
            for m in mem._memories:
                content = m.get("content", "").lower()
                if any(w in content for w in desc_words if len(w) > 3):
                    evidence.append({
                        "source": "memory",
                        "content": m.get("content", "")[:200],
                        "timestamp": m.get("timestamp", ""),
                        "relevance": 0.8 if description in content else 0.6,
                    })
        except Exception:
            pass

        # Scan learning corrections for goal-related topics
        try:
            from core.learning import FridayLearningSystem
            ls = FridayLearningSystem()
            corrections = ls.get_all_corrections()
            for c in corrections:
                if any(w in c.get("original", "").lower() or w in c.get("correction", "").lower()
                       for w in desc_words if len(w) > 3):
                    evidence.append({
                        "source": "correction",
                        "content": c.get("correction", "")[:200],
                        "timestamp": c.get("timestamp", ""),
                        "relevance": 0.9,
                    })
        except Exception:
            pass

        return evidence

    async def auto_update_from_evidence(self, goal_id: str) -> dict:
        """Automatically log progress if strong evidence found (>0.8 relevance).
        Weak evidence (0.5-0.8) surfaces as a suggestion.
        Never silently logs progress — always tells you what it found.
        """
        evidence = await self.detect_progress_evidence(goal_id)
        if not evidence:
            return {"status": "no_evidence", "message": "No evidence of progress found."}

        strong = [e for e in evidence if e["relevance"] >= 0.8]
        weak = [e for e in evidence if 0.5 <= e["relevance"] < 0.8]

        if strong:
            # Auto-log but tell the user
            note = f"Auto-detected: {strong[0]['source']} — {strong[0]['content'][:100]}"
            await self.update_progress(goal_id, note)
            return {
                "status": "auto_logged",
                "message": f"Logged progress based on {len(strong)} strong evidence item(s).",
                "evidence": strong,
            }
        elif weak:
            return {
                "status": "suggestion",
                "message": f"Found {len(weak)} possible evidence item(s). Want me to log this as progress?",
                "evidence": weak,
            }

        return {"status": "no_evidence", "message": "No evidence of progress found."}
