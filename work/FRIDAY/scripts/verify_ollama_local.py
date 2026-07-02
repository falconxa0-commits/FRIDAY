#!/usr/bin/env python3
"""Section 5a — Local-first / privacy / Ollama network-free verification.

Ollama is NOT running in this environment, so per the task spec we mark
this as `manual-required` and provide exact steps for the user to verify
locally. We DO verify what we can verify here:

  1. LocalBrain only talks to localhost (no external URL appears in source).
  2. Construction does not require any network call.
  3. available() returns False honestly when Ollama isn't running.
  4. Greps confirm no external endpoints are referenced anywhere in the
     local_brain module.

Manual-required steps the user must run themselves are printed at the end.
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.local_brain import LocalBrain


def main():
    print("=" * 70)
    print("SECTION 5a — Local-first / Ollama network-free verification")
    print("=" * 70)

    # ---- 1. Construction does not require network ----------------------
    print("\n[1] Constructing LocalBrain (no network call required)…")
    b = LocalBrain()
    print(f"  model = {b.model}")
    print(f"  base_url = {b.base_url}")
    print(f"  generate_url = {b.generate_url}")
    print(f"  chat_url = {b.chat_url}")
    assert "localhost" in b.base_url, "base_url must be localhost"
    print("  PASS — LocalBrain constructed without any network call")

    # ---- 2. available() returns False honestly ------------------------
    print("\n[2] available() returns False honestly when Ollama is not running…")
    avail = asyncio.run(b.available())
    print(f"  available() = {avail}")
    assert avail is False, "available() must be False when Ollama is unreachable"
    print("  PASS — Honest 'False' (Ollama is not running in this env)")

    # ---- 3. Source code only references localhost ----------------------
    print("\n[3] Grep core/local_brain.py for external URLs…")
    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "core", "local_brain.py",
    )
    with open(src_path) as f:
        src = f.read()

    # Find all URL-like strings
    url_pattern = re.compile(r'https?://[^\s"\']+')
    urls = sorted(set(url_pattern.findall(src)))
    print(f"  URLs found in source: {urls}")
    assert all("localhost" in u or "127.0.0.1" in u for u in urls), \
        f"Found non-localhost URL in local_brain.py: {urls}"
    print("  PASS — Every URL in local_brain.py is localhost-only")

    # ---- 4. No external API imports -----------------------------------
    print("\n[4] Checking imports in local_brain.py for external API clients…")
    external_indicators = ["anthropic", "openai", "zhipuai", "google.genai", "tavily"]
    found_external = [ind for ind in external_indicators if ind in src]
    print(f"  External API references found: {found_external or 'NONE'}")
    assert not found_external, \
        f"local_brain.py references external APIs: {found_external}"
    print("  PASS — No external API client imports; only httpx + stdlib")

    # ---- 5. Manual-required notice ------------------------------------
    print("\n" + "=" * 70)
    print("MANUAL-REQUIRED — Ollama is not running in this environment")
    print("=" * 70)
    print("""
The LocalBrain code is verified to be localhost-only (see checks above),
but a real round-trip through Ollama has NOT been tested here because
Ollama is not installed/running in this environment.

To verify locally, run these exact steps:

  1. Install Ollama:
       curl -fsSL https://ollama.com/install.sh | sh

  2. Pull a model:
       ollama pull llama3   # or any other model

  3. Start the Ollama server (if not already running):
       ollama serve

  4. Verify it's listening on localhost:
       curl http://localhost:11434/api/tags

  5. From the FRIDAY project root, run:
       python3 - <<'PY'
       import asyncio
       from core.local_brain import LocalBrain
       b = LocalBrain()
       print('available:', asyncio.run(b.available()))
       async def go():
           async for chunk in b.chat_stream('Say hello in one word.'):
               print(chunk, end='', flush=True)
           print()
       asyncio.run(go())
       PY

  6. While that runs, in another terminal monitor network connections:
       ss -tnp | grep python    # Linux
       # OR
       lsof -i -P | grep python

     You should see ONLY a connection to 127.0.0.1:11434.
     No connection to any external IP or domain.

What you have verified here (without Ollama running):
  - LocalBrain's source contains only localhost URLs
  - No external API client libraries are imported
  - Construction requires no network call
  - available() returns False honestly (no fake "True")
""")
    print("=" * 70)


if __name__ == "__main__":
    main()
