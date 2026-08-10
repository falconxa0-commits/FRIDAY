"""Tests for the release pipeline."""
import asyncio
import pytest

from core.release_pipeline import (
    ReleasePipeline, Release, ReleaseType, ReleaseStatus,
    get_release_pipeline,
)


@pytest.fixture()
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
    import core.release_pipeline
    core.release_pipeline._pipeline = None
    pipe = ReleasePipeline(base_dir=tmp_path / "releases")
    yield pipe
    core.release_pipeline._pipeline = None


class TestReleaseCreation:
    def test_create_alpha_release(self, pipeline):
        release = asyncio.run(pipeline.create_release(
            version="0.1.0",
            release_type=ReleaseType.ALPHA,
            release_notes="First alpha",
        ))
        assert release.version == "0.1.0"
        assert release.release_type == ReleaseType.ALPHA
        assert release.status == ReleaseStatus.DRAFT

    def test_create_duplicate_version_fails(self, pipeline):
        asyncio.run(pipeline.create_release(
            version="1.0.0",
            release_type=ReleaseType.ALPHA,
        ))
        with pytest.raises(ValueError, match="already exists"):
            asyncio.run(pipeline.create_release(
                version="1.0.0",
                release_type=ReleaseType.ALPHA,
            ))

    def test_invalid_transition_rejected(self, pipeline):
        asyncio.run(pipeline.create_release(
            version="1.0.0",
            release_type=ReleaseType.STABLE,
        ))
        with pytest.raises(ValueError, match="Invalid transition"):
            asyncio.run(pipeline.create_release(
                version="1.1.0",
                release_type=ReleaseType.ALPHA,
                predecessor="1.0.0",
            ))


class TestReleaseLifecycle:
    def test_publish_draft(self, pipeline):
        asyncio.run(pipeline.create_release(
            version="1.0.0",
            release_type=ReleaseType.STABLE,
        ))
        published = asyncio.run(pipeline.publish("1.0.0"))
        assert published.status == ReleaseStatus.PUBLISHED

    def test_yank_published(self, pipeline):
        asyncio.run(pipeline.create_release(
            version="1.0.0",
            release_type=ReleaseType.STABLE,
        ))
        asyncio.run(pipeline.publish("1.0.0"))
        yanked = asyncio.run(pipeline.yank("1.0.0", reason="Critical bug"))
        assert yanked.status == ReleaseStatus.YANKED
        assert yanked.metadata["yank_reason"] == "Critical bug"


class TestReleaseListing:
    def test_list_releases_sorted_by_date(self, pipeline):
        asyncio.run(pipeline.create_release(version="1.0.0", release_type=ReleaseType.STABLE))
        asyncio.run(pipeline.create_release(version="1.1.0", release_type=ReleaseType.HOTFIX,
                                            predecessor="1.0.0"))
        releases = asyncio.run(pipeline.list_releases())
        assert len(releases) == 2

    def test_get_latest_returns_most_recent_published(self, pipeline):
        asyncio.run(pipeline.create_release(version="1.0.0", release_type=ReleaseType.STABLE))
        asyncio.run(pipeline.publish("1.0.0"))
        latest = asyncio.run(pipeline.get_latest())
        assert latest is not None
        assert latest.version == "1.0.0"


class TestPersistence:
    def test_releases_survive_restart(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FRIDAY_ENGINEERING_DIR", str(tmp_path))
        import core.release_pipeline
        core.release_pipeline._pipeline = None

        p1 = ReleasePipeline(base_dir=tmp_path / "releases")
        asyncio.run(p1.create_release(version="1.0.0", release_type=ReleaseType.STABLE))

        # Restart
        core.release_pipeline._pipeline = None
        p2 = ReleasePipeline(base_dir=tmp_path / "releases")
        release = asyncio.run(p2.get_release("1.0.0"))
        assert release is not None
        assert release.version == "1.0.0"
