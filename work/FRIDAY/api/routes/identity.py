"""Identity API routes — expose the 5 configurable identity modes.

Endpoints:
    GET  /api/identity          — list all available modes + current mode
    POST /api/identity/{mode}   — switch to a specific identity mode
"""
import logging
from fastapi import APIRouter, HTTPException

logger = logging.getLogger("friday.api.identity")

router = APIRouter()

_current_mode = "General"


@router.get("")
async def get_identity():
    """Return current identity mode and all available modes."""
    from config.identities import FRIDAY_IDENTITIES, DEFAULT_IDENTITY
    return {
        "current_mode": _current_mode,
        "available_modes": {
            name: {k: v for k, v in cfg.items()}
            for name, cfg in FRIDAY_IDENTITIES.items()
        },
    }


@router.post("/{mode}")
async def set_identity(mode: str):
    """Switch to a specific identity mode."""
    global _current_mode
    from config.identities import FRIDAY_IDENTITIES, apply_identity

    if mode not in FRIDAY_IDENTITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown identity mode: {mode}. Available: {list(FRIDAY_IDENTITIES.keys())}"
        )

    old_mode = _current_mode
    _current_mode = mode
    cfg = FRIDAY_IDENTITIES[mode]

    # Apply to the singleton brain if it exists
    try:
        from api.main import _get_brain
        brain = await _get_brain()
        apply_identity(brain, mode)
        logger.info(f"Identity switched: {old_mode} → {mode}")
    except Exception as exc:
        logger.warning(f"Could not apply identity to brain: {exc}")

    return {
        "status": "success",
        "old_mode": old_mode,
        "new_mode": mode,
        "config": cfg,
    }
