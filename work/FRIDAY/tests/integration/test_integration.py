"""Integration tests — require real credentials to run.

Mark with @pytest.mark.integration and skip by default.
Run with: pytest tests/integration/ -m integration
"""
import os
import pytest

# Skip all integration tests by default
pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Set RUN_INTEGRATION_TESTS=1 to run integration tests",
)


class TestGLMIntegration:
    """Real GLM API call — requires GLM_API_KEY."""

    @pytest.mark.asyncio
    async def test_glm_chat(self):
        if not os.getenv("GLM_API_KEY"):
            pytest.skip("GLM_API_KEY not set")
        from core.glm_brain import GLMBrain
        brain = GLMBrain()
        assert brain.available()
        result = ""
        async for chunk in brain.chat_stream("Say 'hello' in one word."):
            result += chunk
        assert len(result) > 0


class TestMCPClient:
    """Real MCP client call to Friday's MCP server."""

    @pytest.mark.asyncio
    async def test_mcp_initialize(self):
        # Start MCP server as subprocess, send initialize, verify response
        import asyncio
        import json
        import sys

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "mcp_server.py",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env={**os.environ, "PYTHONPATH": "."},
        )
        try:
            req = json.dumps({"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1}) + "\n"
            proc.stdin.write(req.encode())
            await proc.stdin.drain()
            resp = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
            data = json.loads(resp.decode())
            assert data.get("result", {}).get("serverInfo", {}).get("name") == "friday-mcp"
        finally:
            proc.terminate()


class TestWebhookReceiver:
    """Real webhook delivery to Friday's endpoint."""

    def test_github_webhook(self):
        import hmac
        import hashlib
        import json
        from fastapi.testclient import TestClient
        os.environ["FRIDAY_API_TOKEN"] = ""
        os.environ["FRIDAY_DEV_MODE"] = "1"
        from api.main import app
        client = TestClient(app)

        payload = json.dumps({
            "action": "opened",
            "pull_request": {"title": "Test PR", "html_url": "https://github.com/test/repo/pull/1"},
        }).encode()

        r = client.post(
            "/api/webhooks/github",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "pull_request",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["event"] == "pull_request"
        assert data["action"] == "opened"
        assert data["pr_title"] == "Test PR"
