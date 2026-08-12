"""Council Mode — Route the same prompt to multiple LLM providers in parallel.

GLM is always available (free tier), so the council always has at least one
voice.  If additional providers (Claude, Gemini, GPT) are configured they
join the council automatically.

Usage::

    from core.council_mode import run_council

    result = await run_council("Explain quantum entanglement in simple terms")
    # result = {
    #     "prompt": "...",
    #     "responses": {"glm": "...", "claude": "..."},
    #     "comparison": {
    #         "agreements": [...],
    #         "disagreements": [...],
    #         "unique_points": {"glm": [...], "claude": [...]},
    #     },
    # }
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from config.settings import (
    ANTHROPIC_API_KEY,
    GEMINI_API_KEY,
    GLM_API_KEY,
    OPENAI_API_KEY,
)

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Provider helpers
# ------------------------------------------------------------------

async def _call_glm(prompt: str) -> Dict[str, Any]:
    """Call GLMBrain and return its full text response."""
    try:
        from core.glm_brain import GLMBrain

        brain = GLMBrain()
        if not brain.available():
            return {"provider": "glm", "response": None, "error": "GLM not available"}

        full_text = ""
        async for chunk in brain.chat_stream(prompt):
            full_text += chunk

        return {"provider": "glm", "response": full_text, "error": None}
    except Exception as exc:
        logger.error("Council: GLM call failed: %s", exc)
        return {"provider": "glm", "response": None, "error": str(exc)}


async def _call_claude(prompt: str) -> Dict[str, Any]:
    """Call Claude and return its full text response."""
    if not ANTHROPIC_API_KEY:
        return {"provider": "claude", "response": None, "error": "No ANTHROPIC_API_KEY"}

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        def _sync_call():
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text

        text = await asyncio.to_thread(_sync_call)
        return {"provider": "claude", "response": text, "error": None}
    except Exception as exc:
        logger.error("Council: Claude call failed: %s", exc)
        return {"provider": "claude", "response": None, "error": str(exc)}


async def _call_gemini(prompt: str) -> Dict[str, Any]:
    """Call Gemini and return its full text response."""
    if not GEMINI_API_KEY:
        return {"provider": "gemini", "response": None, "error": "No GEMINI_API_KEY"}

    try:
        from core.gemini_brain import GeminiBrain

        brain = GeminiBrain()
        if brain._client is None:
            return {"provider": "gemini", "response": None, "error": "Gemini client not initialised"}

        full_text = ""
        async for chunk in brain.chat_stream(prompt):
            full_text += chunk

        return {"provider": "gemini", "response": full_text, "error": None}
    except Exception as exc:
        logger.error("Council: Gemini call failed: %s", exc)
        return {"provider": "gemini", "response": None, "error": str(exc)}


async def _call_gpt(prompt: str) -> Dict[str, Any]:
    """Call GPT (OpenAI) and return its full text response."""
    if not OPENAI_API_KEY:
        return {"provider": "gpt", "response": None, "error": "No OPENAI_API_KEY"}

    try:
        from openai import OpenAI

        client = OpenAI(api_key=OPENAI_API_KEY)

        def _sync_call():
            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content

        text = await asyncio.to_thread(_sync_call)
        return {"provider": "gpt", "response": text, "error": None}
    except Exception as exc:
        logger.error("Council: GPT call failed: %s", exc)
        return {"provider": "gpt", "response": None, "error": str(exc)}


# Mapping from provider name → async callable
_PROVIDER_FUNCS = {
    "glm": _call_glm,
    "claude": _call_claude,
    "gemini": _call_gemini,
    "gpt": _call_gpt,
}


# ------------------------------------------------------------------
# Comparison logic
# ------------------------------------------------------------------

def compare_responses(responses: Dict[str, str]) -> Dict[str, Any]:
    """Identify agreements and disagreements across provider responses.

    This uses a simple keyword / sentence overlap heuristic rather than
    an additional LLM call, so it works even when only one provider
    responded.

    Args:
        responses: Mapping of provider name → response text (non-None).

    Returns:
        Dict with ``agreements``, ``disagreements``, and
        ``unique_points`` keys.
    """
    providers = list(responses.keys())

    if len(providers) < 2:
        # Only one voice — no comparison possible
        return {
            "agreements": [],
            "disagreements": [],
            "unique_points": {p: [] for p in providers},
            "note": "Only one provider responded; comparison requires at least two.",
        }

    # Tokenise responses into sentences (crude but sufficient)
    provider_sentences = {
        p: _split_sentences(responses[p]) for p in providers
    }

    agreements, unique_points = _find_agreements_and_unique(
        providers, provider_sentences,
    )
    disagreements = _find_disagreements(providers, provider_sentences)

    return {
        "agreements": agreements[:10],
        "disagreements": disagreements[:10],
        "unique_points": {p: pts[:5] for p, pts in unique_points.items()},
    }


def _split_sentences(text: str) -> List[str]:
    """Split a response into sentences longer than the fragment threshold.

    The splitter is intentionally crude — newlines become soft sentence
    boundaries and anything shorter than 15 characters is discarded.
    """
    parts = text.replace("\n", ". ").split(". ")
    return [s.strip() for s in parts if len(s.strip()) > 15]


def _find_agreements_and_unique(
    providers: List[str],
    provider_sentences: Dict[str, List[str]],
) -> tuple:
    """Walk every sentence and decide whether it's an agreement or unique.

    Returns ``(agreements, unique_points)`` where ``agreements`` is a list
    of sentences that appear in ≥ 2 providers (deduped, preserving the
    first-seen order) and ``unique_points`` is ``{provider: [sentences]}``.
    """
    agreements: List[str] = []
    unique_points: Dict[str, List[str]] = {p: [] for p in providers}

    for p in providers:
        for sentence in provider_sentences[p]:
            words = set(sentence.lower().split())
            if len(words) < 4:
                continue  # Skip very short fragments

            found_in = _find_sentence_matches(
                p, sentence, words, providers, provider_sentences,
            )
            if len(found_in) >= 2:
                # Avoid duplicate agreement entries
                if sentence not in agreements:
                    agreements.append(sentence)
            else:
                unique_points[p].append(sentence)

    return agreements, unique_points


def _find_sentence_matches(
    source_provider: str,
    sentence: str,
    words: set,
    providers: List[str],
    provider_sentences: Dict[str, List[str]],
) -> List[str]:
    """Return the list of providers whose responses contain ``sentence``.

    Includes ``source_provider`` itself plus every other provider whose
    sentence set overlaps ≥ 50% of the smaller word set (a Jaccard-like
    threshold). Stops at the first match per other provider.
    """
    found_in = [source_provider]
    for other_p in providers:
        if other_p == source_provider:
            continue
        for other_sentence in provider_sentences[other_p]:
            other_words = set(other_sentence.lower().split())
            # Jaccard-like overlap: at least 50% of words match
            overlap = words & other_words
            if len(overlap) >= 0.5 * min(len(words), len(other_words)):
                found_in.append(other_p)
                break
    return found_in


def _find_disagreements(
    providers: List[str],
    provider_sentences: Dict[str, List[str]],
) -> List[str]:
    """Detect explicit contradiction cues across each provider's sentences.

    Returns a list of ``"[provider] sentence"`` strings — capped at 10 by
    the caller. Uses a fixed list of cues that signal a provider is
    explicitly contradicting another (``"however"``, ``"on the other
    hand"``, etc.).
    """
    contradiction_cues = [
        "however", "on the other hand", "conversely", "in contrast",
        "but actually", "incorrect", "not true", "wrong",
    ]
    disagreements: List[str] = []
    for p in providers:
        for sentence in provider_sentences[p]:
            lower = sentence.lower()
            if any(cue in lower for cue in contradiction_cues):
                disagreements.append(f"[{p}] {sentence}")
    return disagreements


# ------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------

async def run_council(
    prompt: str,
    providers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Route the same prompt to multiple providers in parallel.

    Args:
        prompt: The text prompt to send to every provider.
        providers: List of provider names to consult.  Defaults to all
            configured providers (GLM is always included).

    Returns:
        Dict with:
            - ``prompt``: the original prompt
            - ``responses``: dict of provider → response text
            - ``errors``: dict of provider → error message
            - ``comparison``: structured comparison result
    """
    # Determine which providers to use
    if providers is None:
        # Always include GLM (free); include others only if keys are set
        providers = ["glm"]
        if ANTHROPIC_API_KEY:
            providers.append("claude")
        if GEMINI_API_KEY:
            providers.append("gemini")
        if OPENAI_API_KEY:
            providers.append("gpt")

    # Ensure GLM is always present
    if "glm" not in providers:
        providers.insert(0, "glm")

    logger.info("Council mode: routing prompt to %s", providers)

    # Dispatch all calls in parallel
    coroutines = []
    for p in providers:
        func = _PROVIDER_FUNCS.get(p)
        if func is None:
            logger.warning("Council: unknown provider '%s' — skipping", p)
            continue
        coroutines.append(func(prompt))

    results = await asyncio.gather(*coroutines, return_exceptions=True)

    # Collect responses and errors
    responses: Dict[str, str] = {}
    errors: Dict[str, str] = {}

    for result in results:
        if isinstance(result, Exception):
            logger.error("Council: provider raised exception: %s", result)
            continue
        provider = result["provider"]
        if result["response"] is not None:
            responses[provider] = result["response"]
        if result["error"] is not None:
            errors[provider] = result["error"]

    # Compare the successful responses
    comparison = compare_responses(responses)

    return {
        "prompt": prompt,
        "providers_queried": providers,
        "responses": responses,
        "errors": errors,
        "comparison": comparison,
    }
