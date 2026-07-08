"""Self-Improvement API routes — proposals for Friday's own improvement.

Endpoints:
    GET  /api/self-improvement/proposals           — list current proposals
    POST /api/self-improvement/analyze             — trigger analysis
    POST /api/self-improvement/approve/{id}        — approve a proposal through ledger
"""
import logging
from fastapi import APIRouter, HTTPException

logger = logging.getLogger("friday.api.self_improvement")

router = APIRouter()

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        from core.self_improvement import SelfImprovementEngine
        _engine = SelfImprovementEngine()
    return _engine


@router.get("/proposals")
async def get_proposals():
    """Return current improvement proposals."""
    engine = _get_engine()
    return {"proposals": engine.get_proposals(), "count": len(engine.get_proposals())}


@router.post("/analyze")
async def analyze_performance(days: int = 7):
    """Trigger performance analysis and generate proposals."""
    engine = _get_engine()
    analysis = await engine.analyze_performance(days=days)
    proposals = await engine.propose_improvements()
    return {
        "analysis": analysis,
        "new_proposals": len(proposals),
        "all_proposals": engine.get_proposals(),
    }


@router.post("/approve/{proposal_id}")
async def approve_proposal(proposal_id: str):
    """Approve a proposal — goes through the ledger for human approval."""
    engine = _get_engine()
    proposals = engine.get_proposals()
    proposal = next((p for p in proposals if p["id"] == proposal_id), None)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found.")

    # Submit through ledger — requires human approval before any action
    from core.ledger import get_ledger
    ledger = get_ledger()
    action_id = ledger.queue_action(
        "SelfImprovement",
        "apply_proposal",
        {"proposal_id": proposal_id, "type": proposal.get("type"), "title": proposal.get("title")},
        risk_level="medium",
    )
    # The action stays pending — user must approve via /api/actions/{id}/approve
    return {
        "status": "pending_approval",
        "action_id": action_id,
        "proposal_id": proposal_id,
        "message": f"Proposal {proposal_id} queued for human approval. Approve at POST /api/actions/{action_id}/approve",
    }
