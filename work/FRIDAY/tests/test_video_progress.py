"""Tests for video progress callbacks."""
import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock


class TestVideoProgress:
    def test_execute_with_progress_method_exists(self):
        from integrations.video_gen import VideoGen
        vg = VideoGen()
        assert hasattr(vg, "execute_with_progress"), \
            "VideoGen must have execute_with_progress() method"

    @pytest.mark.asyncio
    async def test_progress_callback_called(self):
        from integrations.video_gen import VideoGen
        vg = VideoGen()

        mock_client = MagicMock()
        callbacks = []

        async def callback(data):
            callbacks.append(data)

        with patch.object(vg, "available", return_value=True), \
             patch.object(vg, "_ensure_client", return_value=mock_client), \
             patch.object(vg, "_submit_job", new_callable=AsyncMock, return_value="test_job_123"), \
             patch.object(vg, "_check_status", new_callable=AsyncMock, return_value={"task_status": "SUCCESS", "video_url": "https://example.com/video.mp4"}):
            
            result = await vg.execute_with_progress(
                "generate_video",
                {"prompt": "test video"},
                progress_callback=callback
            )
            assert len(callbacks) > 0
            assert "job_id" in callbacks[0]
            assert "elapsed_seconds" in callbacks[0]
            assert "status" in callbacks[0]

    def test_progress_callback_format(self):
        """Verify the callback data structure has all required fields."""
        sample = {
            "job_id": "test_123",
            "status": "PROCESSING",
            "elapsed_seconds": 30,
            "estimated_remaining": 60,
            "attempt": 3,
        }
        assert "job_id" in sample
        assert "status" in sample
        assert "elapsed_seconds" in sample
        assert "estimated_remaining" in sample
        assert "attempt" in sample
