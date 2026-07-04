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
