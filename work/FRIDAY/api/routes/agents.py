from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from agents.agent_manager import AgentManager, AgentType

router = APIRouter()

class AgentTaskRequest(BaseModel):
    agent_type: str
    prompt: str
    context: Optional[Dict[str, Any]] = None

class SwarmRequest(BaseModel):
    goal: str
    agent_types: Optional[List[str]] = None
    context: Optional[Dict[str, Any]] = None

class PipelineRequest(BaseModel):
    task: str
    context: Optional[Dict[str, Any]] = None

_manager = None
def get_agent_manager():
    global _manager
    if _manager is None:
        _manager = AgentManager()
    return _manager

@router.post("/task")
async def run_agent_task(request: AgentTaskRequest):
    manager = get_agent_manager()
    result = await manager.run_agent(request.agent_type, request.prompt, request.context)
    return {"status": "success", "result": result.to_dict()}

@router.post("/swarm")
async def run_swarm(request: SwarmRequest):
    manager = get_agent_manager()
    results = await manager.run_swarm(request.goal, request.agent_types, request.context)
    return {"status": "success", "results": [r.to_dict() for r in results]}

@router.post("/pipeline")
async def run_pipeline(request: PipelineRequest):
    manager = get_agent_manager()
    result = await manager.run_pipeline(request.task, request.context)
    return {"status": "success", "result": result.to_dict()}

@router.get("/status")
async def get_agent_status():
    manager = get_agent_manager()
    return manager.get_status()

@router.get("/types")
async def get_agent_types():
    return {"agent_types": [at.value for at in AgentType]}
