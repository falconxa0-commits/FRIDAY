"""Goals API routes — set and track personal goals.

Endpoints:
    POST   /api/goals             — set a new goal
    GET    /api/goals             — list all active goals
    PATCH  /api/goals/{id}/progress — update progress
    GET    /api/goals/{id}/nudge  — get Friday's suggestion for next action
    DELETE /api/goals/{id}        — remove a goal
"""
import logging
import uuid
import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("friday.api.goals")

router = APIRouter()

_goals: dict = {}


class GoalRequest(BaseModel):
    description: str
    target_date: str
    type: str = "project"  # learning, project, habit, financial
    metadata: dict = {}


class ProgressRequest(BaseModel):
    progress_note: str


def _get_goal(goal_id: str) -> Optional[dict]:
    return _goals.get(goal_id)


@router.post("")
async def set_goal(req: GoalRequest):
    """Set a new goal."""
    goal_id = f"goal_{uuid.uuid4().hex[:8]}"
    goal = {
        "id": goal_id,
        "description": req.description,
        "target_date": req.target_date,
        "type": req.type,
        "metadata": req.metadata,
        "created_at": datetime.datetime.now().isoformat(),
        "progress_updates": [],
        "status": "active",
    }
    _goals[goal_id] = goal
    return {"status": "success", "goal": goal}


@router.get("")
async def list_goals():
    """List all active goals with progress."""
    result = []
    for g in _goals.values():
        g_copy = dict(g)
        g_copy["progress_count"] = len(g["progress_updates"])
        g_copy["streak_days"] = _compute_streak(g)
        g_copy["last_progress"] = g["progress_updates"][-1] if g["progress_updates"] else None
        result.append(g_copy)
    return {"goals": result, "count": len(result)}


@router.patch("/{goal_id}/progress")
async def update_progress(goal_id: str, req: ProgressRequest):
    """Update progress on a goal."""
    goal = _get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    entry = {
        "note": req.progress_note,
        "timestamp": datetime.datetime.now().isoformat(),
    }
    goal["progress_updates"].append(entry)
    return {"status": "success", "progress_count": len(goal["progress_updates"]), "entry": entry}


@router.get("/{goal_id}/nudge")
async def get_nudge(goal_id: str):
    """Get Friday's suggestion for next action on this goal."""
    goal = _get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    updates = goal["progress_updates"]
    streak = _compute_streak(goal)

    # Generate a real nudge based on actual goal data
    days_left = _days_until(goal["target_date"])
    if days_left is not None and days_left < 0:
        nudge = f"Goal '{goal['description']}' is overdue by {abs(days_left)} days. Consider revising the target date or making significant progress today."
    elif days_left is not None and days_left <= 3:
        nudge = f"Only {days_left} days left for '{goal['description']}'. Focus on the most critical remaining tasks."
    elif streak == 0:
        nudge = f"You haven't logged progress on '{goal['description']}' recently. Even a small update keeps momentum."
    elif streak >= 7:
        nudge = f"Great streak of {streak} days on '{goal['description']}'! Keep it going — what's the next step?"
    else:
        nudge = f"Goal '{goal['description']}' has {len(updates)} progress updates. What can you do today to move forward?"

    return {"goal_id": goal_id, "nudge": nudge, "streak": streak, "days_left": days_left}


@router.delete("/{goal_id}")
async def remove_goal(goal_id: str):
    """Remove a goal."""
    if goal_id not in _goals:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    del _goals[goal_id]
    return {"status": "success", "message": f"Goal {goal_id} removed."}


def _compute_streak(goal: dict) -> int:
    """Compute consecutive days with progress updates."""
    if not goal["progress_updates"]:
        return 0
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


def _days_until(target_date: str) -> Optional[int]:
    """Days until target date (negative = overdue)."""
    try:
        target = datetime.date.fromisoformat(target_date[:10])
        return (target - datetime.date.today()).days
    except (ValueError, TypeError):
        return None
