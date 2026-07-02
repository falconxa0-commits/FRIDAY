"""Scheduler API routes — manage scheduled tasks using singleton brain."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
import datetime
import logging

logger = logging.getLogger("friday.api.scheduler")

router = APIRouter()


class ScheduleRequest(BaseModel):
    name: str
    skill_name: Optional[str] = None
    service: Optional[str] = None
    action: Optional[str] = None
    params: Optional[Dict] = None
    interval_seconds: Optional[int] = None
    run_at_iso: Optional[str] = None


# ---------------------------------------------------------------------------
# Use singleton scheduler
# ---------------------------------------------------------------------------

def _get_scheduler():
    from core.scheduler import get_scheduler
    return get_scheduler()


# ---------------------------------------------------------------------------
# GET /api/scheduler/  — list tasks
# ---------------------------------------------------------------------------

@router.get("/")
async def list_tasks():
    scheduler = _get_scheduler()
    tasks = scheduler.get_tasks()
    serializable = []
    for t in tasks:
        st = t.copy()
        st.pop("func", None)
        if st.get("run_at"):
            st["run_at"] = st["run_at"].isoformat() if hasattr(st["run_at"], "isoformat") else str(st["run_at"])
        if st.get("last_run"):
            st["last_run"] = st["last_run"].isoformat() if hasattr(st["last_run"], "isoformat") else str(st["last_run"])
        serializable.append(st)
    return {"tasks": serializable}


# ---------------------------------------------------------------------------
# POST /api/scheduler/  — create a task (uses singleton brain, not new one)
# ---------------------------------------------------------------------------

@router.post("/")
async def create_task(req: ScheduleRequest):
    """Create a scheduled task that reuses the singleton brain."""
    from api.main import _get_brain

    run_at = None
    if req.run_at_iso:
        run_at = datetime.datetime.fromisoformat(req.run_at_iso)

    async def task_wrapper(**params):
        # Get the singleton brain instead of creating a new one
        brain = await _get_brain()
        if req.skill_name:
            if req.skill_name in brain.skills:
                await brain.skills[req.skill_name].run(brain, params)
        elif req.service and req.action:
            await brain.connector.execute_action(req.service, req.action, params)

    scheduler = _get_scheduler()
    tid = await scheduler.add_task(
        name=req.name,
        coroutine_func=task_wrapper,
        interval_seconds=req.interval_seconds,
        run_at=run_at,
        params=req.params,
    )
    return {"status": "success", "task_id": tid}


# ---------------------------------------------------------------------------
# DELETE /api/scheduler/{task_id}  — remove a task
# ---------------------------------------------------------------------------

@router.delete("/{task_id}")
async def delete_task(task_id: str):
    scheduler = _get_scheduler()
    if scheduler.remove_task(task_id):
        return {"status": "success"}
    raise HTTPException(status_code=404, detail="Task not found")
