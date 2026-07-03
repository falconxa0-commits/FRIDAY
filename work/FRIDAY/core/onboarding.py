"""FRIDAY Onboarding — plain-English setup flow.

Walks a new user through:
1. What Friday is (plain English)
2. GLM_API_KEY — free at open.bigmodel.cn
3. Test GLM call
4. What's available with the key
5. Optional paid upgrades
6. Optional integrations
7. Z.ai free tier rate limits
"""

import asyncio
import logging
import os
import platform
from typing import Optional

logger = logging.getLogger(__name__)


class AutoOnboarding:
    """Interactive onboarding flow for FRIDAY AI Assistant."""

    def __init__(self):
        self.system_info = {
            "platform": platform.system(),
            "version": platform.version(),
            "processor": platform.processor(),
        }

    # ------------------------------------------------------------------
    # Step 1: What is Friday?
    # ------------------------------------------------------------------

    @staticmethod
    def what_is_friday() -> str:
        return (
            "FRIDAY is a personal AI assistant that runs on your machine.\n"
            "It can chat with you, search the web, generate images and videos,\n"
            "manage your calendar, control smart home devices, and more.\n\n"
            "It supports multiple AI providers (GLM, Claude, Gemini, GPT),\n"
            "but works best with GLM because it's free and built-in.\n"
        )

    # ------------------------------------------------------------------
    # Step 2: GLM API Key
    # ------------------------------------------------------------------

    @staticmethod
    def glm_key_instructions() -> str:
        return (
            "To get started, you need a GLM API key from ZhipuAI (Z.ai).\n"
            "It's free — here's how:\n\n"
            "  1. Go to https://open.bigmodel.cn\n"
            "  2. Create a free account\n"
            "  3. Go to API Keys in your dashboard\n"
            "  4. Create a new key and copy it\n\n"
            "Set it as an environment variable:\n"
            "  export GLM_API_KEY='your-key-here'\n"
        )

    # ------------------------------------------------------------------
    # Step 3: Test GLM call
    # ------------------------------------------------------------------

    async def test_glm_call(self, api_key: Optional[str] = None) -> dict:
        """Make one test GLM call to verify the key works.

        Returns:
            Dict with 'success' (bool) and 'message' (str).
        """
        key = api_key or os.getenv("GLM_API_KEY")
        if not key:
            return {
                "success": False,
                "message": "No GLM_API_KEY provided. Set it and try again.",
            }

        try:
            from core.glm_brain import GLMBrain

            brain = GLMBrain(api_key=key)
            if not brain.available():
                return {
                    "success": False,
                    "message": "GLM client could not be initialised. Check your API key.",
                }

            # Simple test prompt
            response_text = ""
            async for chunk in brain.chat_stream("Say 'Hello' in one word."):
                response_text += chunk

            if response_text.strip():
                return {
                    "success": True,
                    "message": f"GLM is working! Test response: {response_text.strip()[:100]}",
                }
            else:
                return {
                    "success": False,
                    "message": "GLM responded with empty text. Your key may be invalid.",
                }

        except ImportError:
            return {
                "success": False,
                "message": "zhipuai package not installed. Run: pip install zhipuai",
            }
        except Exception as exc:
            return {
                "success": False,
                "message": f"GLM test call failed: {exc}",
            }

    # ------------------------------------------------------------------
    # Step 4: What's available with the key
    # ------------------------------------------------------------------

    @staticmethod
    def available_features() -> str:
        return (
            "With your GLM API key, you have access to:\n\n"
            "  - Chat (GLM-4-Flash) — unlimited free conversations\n"
            "  - Web Search — real-time web results built into GLM\n"
            "  - Image Generation (CogView-3) — create images from text\n"
            "  - Video Generation (CogVideoX) — create short videos from text\n"
            "  - Vision Analysis (GLM-4V) — describe and analyze images\n"
            "  - Code Interpreter — run Python code via GLM\n"
            "  - Deep Research — multi-step web research with synthesis\n"
            "  - Council Mode — compare responses from multiple providers\n"
            "  - Tamper-Evident Audit Log — cryptographically chained logs\n"
            "  - Cost Tracking — monitor token usage and estimated costs\n"
        )

    # ------------------------------------------------------------------
    # Step 5: Optional paid upgrades
    # ------------------------------------------------------------------

    @staticmethod
    def paid_upgrades() -> str:
        return (
            "Optional paid upgrades (not required — Friday works for free with GLM):\n\n"
            "  - Claude (Anthropic): $0.003/1K input, $0.015/1K output\n"
            "    Best for: Long-form writing, complex reasoning\n"
            "    Set ANTHROPIC_API_KEY to enable\n\n"
            "  - GPT-4o (OpenAI): $0.0015/1K input, $0.006/1K output\n"
            "    Best for: General-purpose, code generation\n"
            "    Set OPENAI_API_KEY to enable\n\n"
            "  - Gemini 2.0 Flash (Google): $0.000075/1K input\n"
            "    Best for: Fast, cheap responses, multimodal\n"
            "    Set GEMINI_API_KEY to enable\n\n"
            "All providers can be used simultaneously in Council Mode.\n"
        )

    # ------------------------------------------------------------------
    # Step 6: Optional integrations
    # ------------------------------------------------------------------

    @staticmethod
    def optional_integrations() -> str:
        return (
            "Optional integrations (set environment variables to enable):\n\n"
            "  - Weather: OPENWEATHERMAP_API_KEY\n"
            "  - Spotify: SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET\n"
            "  - Smart Home: HOME_ASSISTANT_TOKEN\n"
            "  - Calendar: Google OAuth credentials\n"
            "  - Email (Gmail): Google OAuth credentials\n"
            "  - Voice: PICOVOICE_ACCESS_KEY + ELEVENLABS_API_KEY\n\n"
            "These are all optional. Friday works without them.\n"
        )

    # ------------------------------------------------------------------
    # Step 7: Z.ai free tier rate limits
    # ------------------------------------------------------------------

    @staticmethod
    def rate_limit_info() -> str:
        return (
            "Z.ai Free Tier Rate Limits:\n\n"
            "  - GLM-4-Flash: ~60 requests/min, 100K tokens/day (FREE)\n"
            "  - GLM-4V: ~50 requests/min, 8K context (FREE)\n"
            "  - CogView-3 (images): Limited generations/day (FREE)\n"
            "  - CogVideoX (videos): Limited generations/day (FREE)\n"
            "  - Web Search: Included with GLM-4 (FREE)\n\n"
            "Rate limits vary by account. Visit https://open.bigmodel.cn/pricing\n"
            "for current limits and paid tier options.\n"
        )

    # ------------------------------------------------------------------
    # Full onboarding flow
    # ------------------------------------------------------------------

    async def run_onboarding(self) -> str:
        """Run the full onboarding flow and return a summary string.

        This is designed to be called from the CLI or API startup.
        """
        sections = [
            ("What is FRIDAY?", self.what_is_friday()),
            ("Getting Your API Key", self.glm_key_instructions()),
        ]

        # Test GLM if key is available
        glm_key = os.getenv("GLM_API_KEY")
        if glm_key:
            test_result = await self.test_glm_call(glm_key)
            sections.append(("GLM Connection Test", test_result["message"]))
        else:
            sections.append(
                ("GLM Connection Test",
                 "Skipped — no GLM_API_KEY set. Set it and restart to test.")
            )

        sections.extend([
            ("Available Features", self.available_features()),
            ("Optional Paid Upgrades", self.paid_upgrades()),
            ("Optional Integrations", self.optional_integrations()),
            ("Rate Limits", self.rate_limit_info()),
        ])

        # Build the output
        lines = []
        lines.append("=" * 60)
        lines.append("FRIDAY AI Assistant — Setup")
        lines.append("=" * 60)
        for title, content in sections:
            lines.append(f"\n## {title}\n")
            lines.append(content)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Environment scanning (kept for backward compatibility)
    # ------------------------------------------------------------------

    def scan_environment(self):
        """Scan for running applications (informational only)."""
        try:
            import psutil
        except ImportError:
            return []

        detected_apps = []
        apps_to_check = ["chrome", "vscode", "spotify", "slack", "discord"]
        for process in psutil.process_iter(['name']):
            try:
                name = process.info['name'].lower()
                for app in apps_to_check:
                    if app in name and app not in detected_apps:
                        detected_apps.append(app)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return detected_apps

    def get_welcome_message(self, user_name: str = "User") -> str:
        """Return a simple welcome message."""
        apps = self.scan_environment()
        apps_str = ", ".join(apps) if apps else "none detected"
        return (
            f"Hello, {user_name}. FRIDAY is ready. "
            f"Detected apps: {apps_str}. "
            f"Ask me anything to get started."
        )
