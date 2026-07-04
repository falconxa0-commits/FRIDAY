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


# ---------------------------------------------------------------------------
# Tactical Manager — coordinates multiple agents for complex tasks
# ---------------------------------------------------------------------------

class TacticalRequest(BaseModel):
    task: str
    context: Optional[Dict[str, Any]] = None


@router.post("/tactical")
async def run_tactical(request: TacticalRequest):
    """Run tactical coordination — plans agents, dispatches, synthesizes."""
    try:
        from agents.tactical_manager import TacticalManager
        from api.main import _get_brain
        brain = await _get_brain()
        manager = TacticalManager(brain=brain, agent_manager=get_agent_manager())
        result = await manager.coordinate(request.task, request.context)
        return {"status": "success", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Coding Orchestrator — generates multi-file projects
# ---------------------------------------------------------------------------

class GenerateProjectRequest(BaseModel):
    description: str
    context: Optional[Dict[str, Any]] = None


@router.post("/generate-project")
async def generate_project(request: GenerateProjectRequest):
    """Generate a multi-file project from a description."""
    try:
        from agents.coding_orchestrator import CodingOrchestrator
        from api.main import _get_brain
        brain = await _get_brain()
        orchestrator = CodingOrchestrator(brain=brain)
        result = await orchestrator.generate_project(request.description, request.context)
        return {"status": "success", "result": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
