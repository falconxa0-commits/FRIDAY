"""Video Generation Integration — ZhipuAI CogVideoX.

Generates videos from text prompts using the CogVideoX model via
the ZhipuAI SDK.  Requires ``GLM_API_KEY`` to be set.

Note: Video generation is asynchronous — the SDK returns a task ID
that must be polled until the result is ready.
"""

import asyncio
import datetime
import logging
import time
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
    logger.info("zhipuai package not installed — VideoGen will be unavailable")


class VideoGen(BaseIntegration):
    """Generate videos from text descriptions using ZhipuAI CogVideoX."""

    _name = "VideoGen"

    # Polling configuration
    POLL_INTERVAL = 5      # seconds between polls
    MAX_POLL_TIME = 300    # maximum seconds to wait (5 minutes)

    @property
    def name(self) -> str:
        return self._name

    def _ensure_client(self):
        """Lazy-init the ZhipuAI client."""
        if self._client is not None:
            return self._client
        if not self._api_key:
            return None
        try:
            from zhipuai import ZhipuAI
            self._client = ZhipuAI(api_key=self._api_key)
            return self._client
        except Exception:
            return None

    def available(self) -> bool:
        """Return True if CogVideoX video generation is ready to use."""
        return _ZHIPU_AVAILABLE and bool(GLM_API_KEY)

    async def execute(self, action: str, params: Optional[dict] = None) -> dict:
        """Execute a video generation action.

        Supported actions:
            generate_video  — generate a video from a text prompt.

        Params:
            prompt (str): Text description of the desired video.
            quality (str, optional): "quality" or "speed". Default "quality".
        """
        params = params or {}

        if action != "generate_video":
            return self._make_response(
                "not_implemented",
                f"Action '{action}' is not supported by VideoGen. "
                "Supported: generate_video",
            )

        if not self.available():
            return self._make_response(
                "error",
                "VideoGen is unavailable (missing zhipuai package or GLM_API_KEY)",
            )

        prompt = params.get("prompt", "")
        if not prompt:
            return self._make_response("error", "Missing required parameter: prompt")

        quality = params.get("quality", "quality")

        try:
            client = _ZhipuAI(api_key=GLM_API_KEY)

            # Step 1: Submit the video generation task
            def _submit():
                response = client.videos.generations(
                    model="cogvideox",
                    prompt=prompt,
                    quality=quality,
                )
                return response

            response = await asyncio.to_thread(_submit)

            # Extract task ID
            task_id = ""
            if hasattr(response, "id"):
                task_id = response.id
            elif hasattr(response, "task") and hasattr(response.task, "id"):
                task_id = response.task.id

            if not task_id:
                # If the response contains the video directly (some models)
                video_url = ""
                if hasattr(response, "data") and response.data:
                    video_url = response.data[0].url if hasattr(response.data[0], "url") else ""
                return self._make_response(
                    "success",
                    f"Video generated from prompt: {prompt[:100]}",
                    receipt_data={
                        "prompt": prompt,
                        "quality": quality,
                        "video_url": video_url,
                        "model": "cogvideox",
                    },
                )

            # Step 2: Poll until the video is ready
            video_url = await self._poll_for_result(client, task_id)

            if video_url:
                return self._make_response(
                    "success",
                    f"Video generated from prompt: {prompt[:100]}",
                    receipt_data={
                        "prompt": prompt,
                        "quality": quality,
                        "video_url": video_url,
                        "task_id": task_id,
                        "model": "cogvideox",
                    },
                )
            else:
                return self._make_response(
                    "error",
                    f"Video generation timed out for task {task_id}",
                    receipt_data={"task_id": task_id, "model": "cogvideox"},
                )

        except Exception as exc:
            logger.error(f"Video generation failed: {exc}")
            return self._make_response("error", f"Video generation failed: {exc}")

    async def _poll_for_result(self, client, task_id: str) -> Optional[str]:
        """Poll the ZhipuAI API until the video generation task completes.

        Returns the video URL on success, or None on timeout/failure.
        """
        start = time.time()

        while (time.time() - start) < self.MAX_POLL_TIME:
            try:
                def _check():
                    result = client.videos.retrieve(task_id=task_id)
                    return result

                result = await asyncio.to_thread(_check)

                # Check task status
                task_status = ""
                if hasattr(result, "task_status"):
                    task_status = result.task_status
                elif hasattr(result, "status"):
                    task_status = result.status

                if task_status in ("SUCCESS", "SUCCEEDED", "success"):
                    # Extract video URL
                    if hasattr(result, "data") and result.data:
                        return result.data[0].url if hasattr(result.data[0], "url") else ""
                    if hasattr(result, "video_result"):
                        videos = result.video_result
                        if videos and hasattr(videos[0], "url"):
                            return videos[0].url
                    return ""

                elif task_status in ("FAIL", "FAILED", "fail"):
                    logger.error(f"Video generation task {task_id} failed")
                    return None

                # Still processing — wait and poll again
                await asyncio.sleep(self.POLL_INTERVAL)

            except Exception as exc:
                logger.warning(f"Poll error for task {task_id}: {exc}")
                await asyncio.sleep(self.POLL_INTERVAL)

        logger.error(f"Video generation task {task_id} timed out")
        return None

    def list_actions(self) -> list:
        return ["generate_video"]

    async def execute_with_progress(
        self,
        action: str,
        params: dict,
        progress_callback=None
    ) -> dict:
        """Generate video with real progress callbacks.

        progress_callback receives: {job_id, status, elapsed_seconds, estimated_remaining}
        """
        import time as _time

        if action != "generate_video":
            return await self.execute(action, params)

        if not self.available():
            return self._make_response(
                "not_implemented",
                "GLM_API_KEY not set — video generation unavailable."
            )

        prompt = params.get("prompt", "")
        if not prompt:
            return self._make_response("error", "Missing 'prompt' parameter")

        try:
            # Submit job
            job_id = await self._submit_job(prompt)
            start_time = _time.time()

            for attempt in range(30):
                await asyncio.sleep(10)
                elapsed = int(_time.time() - start_time)
                status = await self._check_status(job_id)

                if progress_callback:
                    try:
                        await progress_callback({
                            "job_id": job_id,
                            "status": status.get("task_status", "PROCESSING"),
                            "elapsed_seconds": elapsed,
                            "estimated_remaining": max(0, 90 - elapsed),
                            "attempt": attempt + 1,
                        })
                    except Exception as e:
                        logger.debug(f"Progress callback error: {e}")

                if status.get("task_status") == "SUCCESS":
                    video_url = status.get("video_url", "")
                    return self._make_response(
                        "success",
                        f"Video generated: {video_url}",
                        receipt_data={
                            "task_id": job_id,
                            "video_url": video_url,
                            "model": "cogvideox",
                            "elapsed_seconds": elapsed,
                        },
                    )
                elif status.get("task_status") == "FAIL":
                    return self._make_response("error", f"Generation failed: {status}")

            return self._make_response("error", f"Timed out after {30*10}s. Job ID: {job_id}")

        except Exception as exc:
            logger.error(f"Video generation with progress failed: {exc}")
            return self._make_response("error", f"Generation failed: {exc}")

    async def _submit_job(self, prompt: str) -> str:
        """Submit a video generation job and return the task ID."""
        client = self._ensure_client()
        if client is None:
            return ""

        def _call():
            response = client.videos.generations.create(
                model="cogvideox",
                prompt=prompt,
            )
            return response

        response = await asyncio.to_thread(_call)
        return getattr(response, "id", getattr(response, "task_id", "unknown"))

    async def _check_status(self, job_id: str) -> dict:
        """Check the status of a video generation job."""
        client = self._ensure_client()
        if client is None:
            return {"task_status": "FAIL", "video_url": ""}

        def _call():
            return client.videos.retrieve_videos_result(id=job_id)

        try:
            response = await asyncio.to_thread(_call)
            task_status = getattr(response, "task_status", "PROCESSING")
            video_url = ""
            if task_status == "SUCCESS":
                video_result = getattr(response, "video_result", [])
                if video_result:
                    video_url = getattr(video_result[0], "url", "")
            return {"task_status": task_status, "video_url": video_url}
        except Exception as e:
            logger.debug(f"Status check error: {e}")
            return {"task_status": "PROCESSING", "video_url": ""}
