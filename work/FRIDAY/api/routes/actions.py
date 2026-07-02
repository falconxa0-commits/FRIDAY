from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from core.ledger import get_ledger

router = APIRouter()
ledger = get_ledger()

@router.get("/")
async def list_pending_actions():
    return {"pending": list(ledger.pending_actions.values())}

@router.post("/{action_id}/approve")
async def approve_action(action_id: str):
    if ledger.approve_action(action_id):
        return {"status": "success", "message": "Action approved."}
    raise HTTPException(status_code=404, detail="Action not found.")

@router.post("/{action_id}/reject")
async def reject_action(action_id: str):
    if ledger.reject_action(action_id):
        return {"status": "success", "message": "Action rejected."}
    raise HTTPException(status_code=404, detail="Action not found.")

@router.get("/verify")
async def verify_ledger_chain():
    """Verify the integrity of the hash-chained audit log.

    Returns {"valid": true} if no entry has been tampered with,
    {"valid": false} otherwise.
    """
    is_valid = ledger.verify_chain()
    return {
        "valid": is_valid,
        "entry_count": len(ledger.get_audit_log()),
        "message": "Chain intact" if is_valid else "Chain BROKEN — tampering detected",
    }

@router.get("/audit")
async def get_audit_log():
    """Return the full hash-chained audit log."""
    return {"entries": ledger.get_audit_log()}
