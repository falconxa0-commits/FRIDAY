import os
import logging
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


def get_env_var(name: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    """Retrieve an environment variable.

    Args:
        name: The name of the environment variable.
        default: Default value if the variable is not set.
        required: If True and the variable is not set (and no default), raise ValueError.

    Returns:
        The value of the environment variable, or default/None.

    Raises:
        ValueError: If required=True and the variable is missing with no default.
    """
    val = os.getenv(name, default)
    if required and not val:
        raise ValueError(f"Required environment variable '{name}' is not set.")
    return val


# --- API Keys ---
ANTHROPIC_API_KEY: Optional[str] = get_env_var("ANTHROPIC_API_KEY")
OPENAI_API_KEY: Optional[str] = get_env_var("OPENAI_API_KEY")
SUPABASE_URL: Optional[str] = get_env_var("SUPABASE_URL")
SUPABASE_KEY: Optional[str] = get_env_var("SUPABASE_KEY")
ELEVENLABS_API_KEY: Optional[str] = get_env_var("ELEVENLABS_API_KEY")
PICOVOICE_ACCESS_KEY: Optional[str] = get_env_var("PICOVOICE_ACCESS_KEY")
GEMINI_API_KEY: Optional[str] = get_env_var("GEMINI_API_KEY")
GLM_API_KEY: Optional[str] = get_env_var("GLM_API_KEY")

# Friday Security
FRIDAY_API_TOKEN: Optional[str] = get_env_var("FRIDAY_API_TOKEN")

# New integrations
OPENWEATHERMAP_API_KEY: Optional[str] = get_env_var("OPENWEATHERMAP_API_KEY")
SPOTIFY_CLIENT_ID: Optional[str] = get_env_var("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET: Optional[str] = get_env_var("SPOTIFY_CLIENT_SECRET")
HOME_ASSISTANT_TOKEN: Optional[str] = get_env_var("HOME_ASSISTANT_TOKEN")

# --- Brain Provider ---
# Determines which LLM provider to use: "glm" (default, free), "claude", "gemini", or "ollama"
BRAIN_PROVIDER: str = get_env_var("BRAIN_PROVIDER", default="glm")

# --- Autonomy Profile ---
# Controls what Friday can do without user approval.
# GUEST:    Everything requires approval.
# STANDARD: Read-only auto-approves, destructive requires approval.
# POWER:    PC/Browser auto-approves, file deletion requires approval.
AUTONOMY_PROFILE: str = get_env_var("AUTONOMY_PROFILE", default="GUEST")

# --- Web Search ---
TAVILY_API_KEY: Optional[str] = get_env_var("TAVILY_API_KEY")

# --- Logging ---
LOG_LEVEL: str = get_env_var("LOG_LEVEL", default="INFO")
LOG_FILE: str = "friday.log"

# --- Workspace (lazy creation) ---
WORKSPACE_ROOT: str = os.path.join(os.getcwd(), "friday_workspace")


def ensure_workspace() -> str:
    """Lazily create the workspace root directory if it doesn't exist.

    Returns:
        The path to the workspace root.
    """
    if not os.path.exists(WORKSPACE_ROOT):
        os.makedirs(WORKSPACE_ROOT, exist_ok=True)
        logger.info(f"Created workspace root: {WORKSPACE_ROOT}")
    return WORKSPACE_ROOT
