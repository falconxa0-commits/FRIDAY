"""Persona export/import — portable Friday setup.

A user can export their entire Friday setup (patterns, memories, skills,
integration config) to a portable file, and import it on a new machine.
Friday follows you across devices with full continuity.

Includes versioning so you can roll back to an earlier state.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


PERSONA_VERSION = "2.0"


def export_persona(
    memory=None,
    pattern_engine=None,
    multimodal_memory=None,
    include_secrets: bool = False,
) -> dict:
    """Export the user's full Friday persona to a portable dict.

    Args:
        memory: FridayMemory instance (text memories)
        pattern_engine: PatternEngine instance (observed patterns)
        multimodal_memory: MultiModalMemory instance (visual memories)
        include_secrets: If False, strips all API keys from integration config.

    Returns:
        A dict that can be JSON-serialised and re-imported on another machine.
    """
    persona: Dict[str, Any] = {
        "version": PERSONA_VERSION,
        "exported_at": datetime.datetime.now().isoformat(),
        "friday_version": "2.0",
    }

    # Text memories
    if memory is not None:
        persona["memories"] = {
            "conversations": list(memory._memories),
            "session_facts": dict(getattr(memory, "_session_facts", {})),
        }
    else:
        persona["memories"] = {"conversations": [], "session_facts": {}}

    # Patterns
    if pattern_engine is not None:
        persona["patterns"] = {
            "interactions": pattern_engine.export_interactions(),
            "discovered_patterns": pattern_engine.patterns,
        }
    else:
        persona["patterns"] = {"interactions": [], "discovered_patterns": []}

    # Visual memories
    if multimodal_memory is not None:
        persona["visual_memories"] = multimodal_memory.list_visual_memories()
    else:
        persona["visual_memories"] = []

    # Integration config (env vars — strip secrets unless include_secrets=True)
    env_keys = [
        "GLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
        "TAVILY_API_KEY", "ELEVENLABS_API_KEY", "PICOVOICE_ACCESS_KEY",
        "OPENWEATHERMAP_API_KEY", "SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET",
        "HOME_ASSISTANT_TOKEN", "STRIPE_SECRET_KEY",
        "SUPABASE_URL", "SUPABASE_KEY",
        "OCTOPRINT_URL", "OCTOPRINT_API_KEY", "MOONRAKER_URL",
        "BRAIN_PROVIDER", "AUTONOMY_PROFILE",
    ]
    env_config: Dict[str, Optional[str]] = {}
    for key in env_keys:
        val = os.getenv(key)
        if val:
            if include_secrets or key in ("BRAIN_PROVIDER", "AUTONOMY_PROFILE",
                                          "SUPABASE_URL", "OCTOPRINT_URL", "MOONRAKER_URL",
                                          "SPOTIFY_CLIENT_ID"):
                env_config[key] = val
            else:
                # Mark as present but don't include the value
                env_config[key] = f"<present:{len(val)} chars>"
        # else: omit entirely
    persona["env_config"] = env_config

    # Skills (just list which are installed)
    skills_dir = Path(__file__).resolve().parent.parent / "skills"
    if skills_dir.is_dir():
        persona["skills"] = sorted([
            f.stem for f in skills_dir.glob("*.py")
            if f.stem not in ("__init__", "base")
        ])
    else:
        persona["skills"] = []

    return persona


def import_persona(persona: dict, memory=None, pattern_engine=None,
                   multimodal_memory=None) -> dict:
    """Import a persona dict and apply it to the given subsystems.

    Returns a summary of what was imported.
    """
    summary = {
        "version": persona.get("version"),
        "imported_at": datetime.datetime.now().isoformat(),
        "memories_imported": 0,
        "patterns_imported": 0,
        "visual_memories_imported": 0,
        "env_vars_set": 0,
        "warnings": [],
    }

    # Memories
    if memory is not None:
        mem_data = persona.get("memories", {})
        for conv in mem_data.get("conversations", []):
            memory._memories.append(conv)
        for k, v in mem_data.get("session_facts", {}).items():
            memory._session_facts[k] = v
        summary["memories_imported"] = len(mem_data.get("conversations", []))

    # Patterns
    if pattern_engine is not None:
        pat_data = persona.get("patterns", {})
        pattern_engine.import_interactions(pat_data.get("interactions", []))
        pattern_engine.patterns = pat_data.get("discovered_patterns", [])
        summary["patterns_imported"] = len(pat_data.get("interactions", []))

    # Visual memories
    if multimodal_memory is not None:
        for vm in persona.get("visual_memories", []):
            multimodal_memory._visual_memories.append(vm)
        summary["visual_memories_imported"] = len(persona.get("visual_memories", []))

    # Env vars — only set the ones that aren't already configured + skip <present:...> placeholders
    env_config = persona.get("env_config", {})
    for key, val in env_config.items():
        if val is None or (isinstance(val, str) and val.startswith("<present:")):
            continue
        if os.getenv(key) is None:  # don't override existing
            os.environ[key] = val
            summary["env_vars_set"] += 1

    return summary


def save_persona_to_file(persona: dict, path: str) -> str:
    """Save a persona dict to a JSON file. Returns the absolute path."""
    abs_path = os.path.abspath(path)
    with open(abs_path, "w") as f:
        json.dump(persona, f, indent=2, default=str)
    return abs_path


def load_persona_from_file(path: str) -> dict:
    """Load a persona dict from a JSON file."""
    with open(path) as f:
        return json.load(f)
