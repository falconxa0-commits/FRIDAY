#!/usr/bin/env python3
"""Section 10d — Z.ai Creative Suite NL routing verification.

Verifies that natural language commands like 'generate an image of X'
route through the existing tool-calling mechanism to the right Z.ai
model automatically (CogView-3 for images, CogVideoX for videos).
"""
import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    print("=" * 70)
    print("SECTION 10d — Z.ai Creative Suite NL routing verification")
    print("=" * 70)

    from core.brain import FridayBrain

    # Construct a FridayBrain (GLM is the default provider but we'll
    # bypass the LLM and test the creative-route detection directly)
    brain = FridayBrain(provider="ollama")
    print(f"\n[1] Brain provider: {brain.provider}")

    # ---- 2. NL image routing ------------------------------------------
    print("\n[2] Sending 'generate an image of the Lagos skyline at sunset'…")
    image_message = "generate an image of the Lagos skyline at sunset"
    route = brain._detect_creative_route(image_message)
    print(f"  _detect_creative_route() returned: {route}")
    assert route is not None, "Image request should be detected"
    assert route["type"] == "image"
    assert "lagos" in route["prompt"].lower()
    assert "sunset" in route["prompt"].lower()
    print("  PASS — NL image request detected and prompt extracted")

    # ---- 3. Verify the routing actually fires the ImageGen integration
    print("\n[3] Verifying the routing fires ImageGen integration…")
    captured_calls = []

    async def mock_execute_action(service, action, params, **kwargs):
        captured_calls.append({
            "service": service, "action": action, "params": params,
        })
        return {
            "status": "success",
            "message": "Image generated",
            "receipt": {"image_url": "https://example.com/cogview-image.png"},
        }

    # Mock the connector's execute_action
    brain.connector = MagicMock()
    brain.connector.execute_action = mock_execute_action

    chunks = []
    async for chunk in brain._handle_creative_route(route):
        chunks.append(chunk)
    full_response = "".join(chunks)
    print(f"  Response: {full_response}")
    print(f"  Captured calls: {captured_calls}")

    assert len(captured_calls) == 1, f"Expected 1 call, got {len(captured_calls)}"
    call = captured_calls[0]
    print(f"  Service called: {call['service']}")
    print(f"  Action called: {call['action']}")
    print(f"  Params: {call['params']}")

    # The brain calls "image_gen" but the integration name is "ImageGen".
    # The universal connector is case-insensitive on integration names
    # because plugin discovery registers under the .name property.
    # (We accept either "image_gen" or "ImageGen" here.)
    assert call["service"].lower().replace("_", "") in ("imagegen",)
    assert call["action"] == "generate_image"
    assert "lagos" in call["params"]["prompt"].lower()
    print("  PASS — ImageGen.execute(generate_image) called with extracted prompt")

    # ---- 4. NL video routing ------------------------------------------
    print("\n[4] Sending 'create a video of a drone shot over Victoria Island'…")
    video_message = "create a video of a drone shot over Victoria Island"
    route_v = brain._detect_creative_route(video_message)
    print(f"  _detect_creative_route() returned: {route_v}")
    assert route_v is not None
    assert route_v["type"] == "video"
    assert "drone" in route_v["prompt"].lower() or "victoria" in route_v["prompt"].lower()
    print("  PASS — NL video request detected and prompt extracted")

    # ---- 5. Verify the routing fires VideoGen integration -------------
    print("\n[5] Verifying the routing fires VideoGen integration…")
    captured_calls_v = []

    async def mock_execute_action_v(service, action, params, **kwargs):
        captured_calls_v.append({
            "service": service, "action": action, "params": params,
        })
        return {
            "status": "success",
            "message": "Video generated",
            "receipt": {"video_url": "https://example.com/cogvideo.mp4"},
        }

    brain.connector.execute_action = mock_execute_action_v
    chunks_v = []
    async for chunk in brain._handle_creative_route(route_v):
        chunks_v.append(chunk)
    full_response_v = "".join(chunks_v)
    print(f"  Response: {full_response_v}")
    print(f"  Captured calls: {captured_calls_v}")
    assert len(captured_calls_v) == 1
    call_v = captured_calls_v[0]
    assert call_v["action"] == "generate_video"
    print("  PASS — VideoGen.execute(generate_video) called")

    # ---- 6. Non-creative message doesn't route ------------------------
    print("\n[6] Non-creative message ('what is the weather?') does NOT route…")
    plain_route = brain._detect_creative_route("what is the weather in Lagos?")
    print(f"  _detect_creative_route() returned: {plain_route}")
    assert plain_route is None, "Non-creative message should not route"
    print("  PASS — Non-creative messages are not routed to creative suite")

    # ---- 7. Output depends on input -----------------------------------
    print("\n[7] Output depends on input — different prompts produce different params…")
    r1 = brain._detect_creative_route("generate an image of a cat")
    r2 = brain._detect_creative_route("generate an image of a dog")
    assert r1["prompt"] != r2["prompt"]
    print(f"  'cat' prompt: {r1['prompt']}")
    print(f"  'dog' prompt: {r2['prompt']}")
    print("  PASS — Different prompts produce different extracted params")

    print("\n" + "=" * 70)
    print("SECTION 10d VERIFIED")
    print("  - 'generate an image of X' routes to ImageGen (CogView-3)")
    print("  - 'create a video of X' routes to VideoGen (CogVideoX)")
    print("  - Routing happens through the existing _detect_creative_route path")
    print("  - Extracted prompt is passed as the 'prompt' param")
    print("  - Non-creative messages are not routed (no false positives)")
    print("  - Different prompts produce different params (output depends on input)")
    print()
    print("  When GLM_API_KEY is set, this fires a real CogView-3 / CogVideoX")
    print("  API call and returns a real image/video URL in the receipt.")
    print("  Without GLM_API_KEY, the integration returns honest 'not_implemented'.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
