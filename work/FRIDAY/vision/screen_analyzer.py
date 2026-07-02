import base64
import json
import logging
from typing import Optional

from core.glm_brain import GLMBrain

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None
    _OPENAI_AVAILABLE = False

from config.settings import OPENAI_API_KEY

logger = logging.getLogger(__name__)


class ScreenAnalyzer:
    def __init__(self):
        self.glm_brain = None
        self.openai_client = None

        # --- Try GLMBrain first (Z.ai native vision) ---
        try:
            glm = GLMBrain()
            if glm.available():
                self.glm_brain = glm
                logger.info("ScreenAnalyzer: using GLMBrain (glm-4v) for vision")
        except Exception as e:
            logger.debug(f"GLMBrain not available for vision: {e}")

        # --- Fallback to OpenAI ---
        if not _OPENAI_AVAILABLE:
            logger.warning("openai package not installed. OpenAI vision fallback unavailable.")
            return
        if not OPENAI_API_KEY:
            logger.warning("OPENAI_API_KEY not set. OpenAI vision fallback will be unavailable.")
            return
        try:
            self.openai_client = OpenAI(api_key=OPENAI_API_KEY)
            if not self.glm_brain:
                logger.info("ScreenAnalyzer: using OpenAI (gpt-4o) for vision")
        except Exception as e:
            logger.warning(f"Failed to initialize OpenAI client: {e}")

    def analyze_screen(self, image_path):
        """Analyze a screenshot using GLM first, falling back to OpenAI."""
        # --- Try GLM vision first ---
        if self.glm_brain:
            try:
                with open(image_path, "rb") as image_file:
                    b64 = base64.b64encode(image_file.read()).decode("utf-8")
                result = self.glm_brain.vision_analyze(
                    b64,
                    prompt="Describe this screen as Friday AI.",
                    model_tier="vision",
                )
                if result and not result.startswith("Error:"):
                    return result
                logger.debug(f"GLM vision returned error, falling back: {result}")
            except Exception as e:
                logger.debug(f"GLM vision failed, falling back to OpenAI: {e}")

        # --- Fallback to OpenAI ---
        if not self.openai_client:
            return "not_implemented: Screen analysis requires GLM_API_KEY or OPENAI_API_KEY."
        try:
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": "Describe this screen as Friday AI."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]}],
                max_tokens=300,
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Error: {e}"

    def find_element(self, description, image_path):
        """Find element coordinates using GLM first, falling back to OpenAI."""
        # --- Try GLM vision first ---
        if self.glm_brain:
            try:
                with open(image_path, "rb") as image_file:
                    b64 = base64.b64encode(image_file.read()).decode("utf-8")
                prompt = (
                    f"Find the center coordinates (x, y) for the element: '{description}'. "
                    "Return ONLY JSON: {\"x\": int, \"y\": int}. "
                    "If not found, return {\"x\": -1, \"y\": -1}."
                )
                result = self.glm_brain.vision_analyze(
                    b64,
                    prompt=prompt,
                    model_tier="vision",
                )
                if result and not result.startswith("Error:"):
                    # Try to extract JSON from the response
                    coords = self._extract_json_coords(result)
                    if coords and coords.get("x") != -1:
                        return coords
                    logger.debug(f"GLM vision could not locate element, falling back")
            except Exception as e:
                logger.debug(f"GLM element localization failed, falling back: {e}")

        # --- Fallback to OpenAI ---
        if not self.openai_client:
            return None
        try:
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')

            prompt = f"Find the center coordinates (x, y) for the element: '{description}'. Return ONLY JSON: {{\"x\": int, \"y\": int}}. If not found, return {{\"x\": -1, \"y\": -1}}."

            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]}],
                max_tokens=100,
                response_format={ "type": "json_object" }
            )
            coords = json.loads(response.choices[0].message.content)
            if coords.get("x") == -1: return None
            return coords
        except Exception as e:
            logging.error(f"Element localization failed: {e}")
            return None

    @staticmethod
    def _extract_json_coords(text: str) -> Optional[dict]:
        """Try to extract a JSON coordinate object from model output."""
        import re
        # Try to find a JSON object in the text
        match = re.search(r'\{[^{}]*"x"\s*:\s*-?\d+[^{}]*"y"\s*:\s*-?\d+[^{}]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except (json.JSONDecodeError, ValueError):
                pass
        # Try parsing the whole text as JSON
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "x" in data and "y" in data:
                return data
        except (json.JSONDecodeError, ValueError):
            pass
        return None
