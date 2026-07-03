"""Image Generation Integration — ZhipuAI CogView-3.

Generates images from text prompts using the CogView-3 model via
the ZhipuAI SDK.  Requires ``GLM_API_KEY`` to be set.
"""

import asyncio
import datetime
import logging
import os
from typing import Optional

from config.settings import GLM_API_KEY
from integrations.base import BaseIntegration

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy import
# ---------------------------------------------------------------------------

_ZhipuAI = None
_ZHIPU_AVAILABLE = False

try:
    from zhipuai import ZhipuAI as _ZhipuClient  # noqa: N813
    _ZhipuAI = _ZhipuClient
    _ZHIPU_AVAILABLE = True
except ImportError:
    logger.info("zhipuai package not installed — ImageGen will be unavailable")


class ImageGen(BaseIntegration):
    """Generate images from text descriptions using ZhipuAI CogView-3."""

    _name = "ImageGen"

    @property
    def name(self) -> str:
        return self._name

    def available(self) -> bool:
        """Return True if CogView-3 image generation is ready to use."""
        return _ZHIPU_AVAILABLE and bool(GLM_API_KEY)

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        """Execute an image generation action.

        Supported actions:
            generate_image  — generate an image from a text prompt.

        Params:
            prompt (str): Text description of the desired image.
            size (str, optional): Image size, e.g. "1024x1024". Default "1024x1024".
        """
        params = params or {}

        if action != "generate_image":
            return self._make_response(
                "not_implemented",
                f"Action '{action}' is not supported by ImageGen. "
                "Supported: generate_image",
            )

        if not self.available():
            return self._make_response(
                "error",
                "ImageGen is unavailable (missing zhipuai package or GLM_API_KEY)",
            )

        prompt = params.get("prompt", "")
        if not prompt:
            return self._make_response("error", "Missing required parameter: prompt")

        size = params.get("size", "1024x1024")

        try:
            client = _ZhipuAI(api_key=GLM_API_KEY)

            def _generate():
                response = client.images.generations(
                    model="cogview-3",
                    prompt=prompt,
                    size=size,
                )
                return response

            response = await asyncio.to_thread(_generate)

            # Extract image URL from response
            image_url = ""
            if hasattr(response, "data") and response.data:
                image_url = response.data[0].url if hasattr(response.data[0], "url") else ""

            return self._make_response(
                "success",
                f"Image generated from prompt: {prompt[:100]}",
                receipt_data={
                    "prompt": prompt,
                    "size": size,
                    "image_url": image_url,
                    "model": "cogview-3",
                },
            )

        except Exception as exc:
            logger.error(f"Image generation failed: {exc}")
            return self._make_response("error", f"Image generation failed: {exc}")

    def list_actions(self) -> list:
        return ["generate_image"]
