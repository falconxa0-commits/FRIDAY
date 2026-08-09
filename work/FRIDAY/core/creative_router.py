"""Creative suite router — image / video generation request detection + dispatch.

Extracted from ``core/brain.py`` (WAVE2-ARCH refactor) to keep ``FridayBrain``
focused on orchestration. ``CreativeRouter`` is responsible for:

1. Detecting whether an incoming user message is an image/video generation
   request (NL pattern matching).
2. Dispatching the detected route to the appropriate integration
   (ImageGen / VideoGen) via the UniversalConnector, which in turn calls
   Z.ai's CogView-3 / CogVideoX.

Public API
----------
``CreativeRouter.detect(message) -> Optional[dict]``
    Returns ``{"type": "image"|"video", "prompt": str}`` or ``None``.

``CreativeRouter.handle(route) -> AsyncGenerator[str, None]``
    Yields the response chunks for the detected route.

Backward compatibility
----------------------
``FridayBrain._detect_creative_route`` and ``FridayBrain._handle_creative_route``
remain as thin delegating wrappershellos so existing callers (e.g.
``scripts/verify_creative_routing.py``) continue to work unchanged.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncGenerator, Dict, Optional

logger = logging.getLogger("FridayBrain")


class CreativeRouter:
    """Routes image/video generation requests to the appropriate integration.

    Parameters
    ----------
    universal_connector:
        The ``UniversalConnector`` instance used to dispatch actions to
        the ``image_gen`` / ``video_gen`` integrations.
    """

    # ------------------------------------------------------------------
    # Pattern tables
    # ------------------------------------------------------------------

    # Patterns that indicate the user wants an image generated
    _IMAGE_PATTERNS = [
        re.compile(r"generate\s+(?:an?\s+)?image\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"create\s+(?:an?\s+)?image\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"draw\s+(?:an?\s+)?(.+)", re.IGNORECASE),
        re.compile(r"make\s+(?:an?\s+)?image\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"illustrate\s+(.+)", re.IGNORECASE),
        re.compile(r"picture\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"show\s+(?:me\s+)?(?:an?\s+)?image\s+(?:of\s+)?(.+)", re.IGNORECASE),
    ]

    # Patterns that indicate the user wants a video generated
    _VIDEO_PATTERNS = [
        re.compile(r"generate\s+(?:an?\s+)?video\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"create\s+(?:an?\s+)?video\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"make\s+(?:an?\s+)?video\s+(?:of\s+)?(.+)", re.IGNORECASE),
        re.compile(r"animate\s+(.+)", re.IGNORECASE),
    ]

    def __init__(self, universal_connector):
        self.connector = universal_connector

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(self, message: str) -> Optional[Dict[str, Any]]:
        """Check if the message is a creative generation request.

        Returns
        -------
        dict or None
            A dict with ``type`` ("image" or "video") and ``prompt`` (the
            extracted generation prompt), or ``None`` if the message is
            not a creative request.
        """
        for pattern in self._IMAGE_PATTERNS:
            match = pattern.search(message)
            if match:
                prompt = match.group(1).strip()
                if prompt:
                    return {"type": "image", "prompt": prompt}

        for pattern in self._VIDEO_PATTERNS:
            match = pattern.search(message)
            if match:
                prompt = match.group(1).strip()
                if prompt:
                    return {"type": "video", "prompt": prompt}

        return None

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def handle(self, route: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """Handle an image or video generation request.

        Uses the UniversalConnector to dispatch to the ImageGen or
        VideoGen integration, which in turn uses GLM's CogView-3 or
        CogVideoX.
        """
        route_type = route["type"]
        prompt = route["prompt"]

        logger.info("Creative route: %s generation for prompt: %s", route_type, prompt)

        if route_type == "image":
            result = await self.connector.execute_action(
                "image_gen", "generate_image", {"prompt": prompt}
            )
        elif route_type == "video":
            result = await self.connector.execute_action(
                "video_gen", "generate_video", {"prompt": prompt}
            )
        else:
            yield f"Unknown creative route type: {route_type}"
            return

        if result.get("status") == "success":
            receipt = result.get("receipt", result)
            # Try to extract URL from receipt
            url = ""
            if isinstance(receipt, dict):
                url = receipt.get("image_url", receipt.get("video_url", ""))

            if url:
                yield f"Here's your {route_type}: {url}"
            else:
                yield (
                    f"{route_type.capitalize()} generated successfully! "
                    f"Details: {json.dumps(receipt, indent=2, default=str)}"
                )
        else:
            error_msg = result.get("message", result.get("error", "Unknown error"))
            yield f"Sorry, {route_type} generation failed: {error_msg}"
