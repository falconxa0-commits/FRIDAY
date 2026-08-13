"""FRIDAY Age V — Civilization Core.

The civilization layer sits on top of the Age IV runtime. It provides:
    - Citizen Registry (identity, roles, lifecycle)
    - Identity Engine (unique IDs, capabilities, trust)
    - Civilization Manager (hierarchy, coordination)
    - Reputation System (performance-based autonomy)

This module is 100% additive — it does NOT modify any Age IV code.
It uses the Age IV EventBus, CapabilityRegistry, and PolicyEngine via
composition, not inheritance.

Design:
    - Every agent, plugin, tool, and model is a "Citizen"
    - Citizens have identity, rank, permissions, and lifecycle
    - The Founder (human) has absolute authority
    - The High Council (AI) has executive authority
    - Governors have departmental authority
    - Specialists have domain authority
    - Workers have execution authority only
"""
from core.civilization.citizen import (
    Citizen, CitizenID, CitizenRank, CitizenStatus, CitizenRegistry,
)
from core.civilization.identity import IdentityEngine
from core.civilization.reputation import ReputationSystem
from core.civilization.manager import CivilizationManager

__all__ = [
    "Citizen", "CitizenID", "CitizenRank", "CitizenStatus", "CitizenRegistry",
    "IdentityEngine", "ReputationSystem", "CivilizationManager",
]
