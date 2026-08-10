"""Release Pipeline — structured release management.

Supports alpha, beta, release candidate, stable, and hotfix releases.
Generates release notes, changelogs, and version summaries.

Design principles:
    - **Structured**: Every release has a version, type, and manifest.
    - **Traceable**: Releases link to tasks and receipts in the KB.
    - **Reproducible**: Release artifacts are deterministic.
    - **Safe**: Hotfixes have a fast-track process.

Release types (in order of progression):
    - ALPHA: Early development, breaking changes expected
    - BETA: Feature-complete, may have bugs
    - RC: Release candidate, only bug fixes allowed
    - STABLE: Production-ready
    - HOTFIX: Emergency fix for a stable release
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("friday.release_pipeline")

_RELEASES_DIR = Path(os.environ.get(
    "FRIDAY_ENGINEERING_DIR",
    str(Path.cwd() / ".friday"),
)) / "releases"
_RELEASES_DIR.mkdir(parents=True, exist_ok=True)


class ReleaseType(str, Enum):
    ALPHA = "alpha"
    BETA = "beta"
    RC = "release_candidate"
    STABLE = "stable"
    HOTFIX = "hotfix"


class ReleaseStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    YANKED = "yanked"  # unpublished due to critical issue


# Valid transitions
VALID_TRANSITIONS: Dict[ReleaseType, List[ReleaseType]] = {
    ReleaseType.ALPHA: [ReleaseType.BETA, ReleaseType.STABLE],
    ReleaseType.BETA: [ReleaseType.RC, ReleaseType.STABLE],
    ReleaseType.RC: [ReleaseType.STABLE],
    ReleaseType.STABLE: [ReleaseType.HOTFIX],
    ReleaseType.HOTFIX: [],  # hotfixes are terminal
}


@dataclass
class Release:
    """A structured release.

    Attributes:
        version: Semantic version string (e.g., "3.2.1").
        release_type: ALPHA/BETA/RC/STABLE/HOTFIX.
        status: DRAFT/PUBLISHED/YANKED.
        release_date: ISO timestamp.
        release_notes: Markdown release notes.
        changelog: Markdown changelog.
        migration_guide: Optional migration guide for breaking changes.
        completed_task_ids: Task IDs included in this release.
        receipt_hashes: Task receipt hashes for verification.
        predecessor: Previous release version this one builds on.
        metadata: Arbitrary metadata.
    """
    version: str
    release_type: ReleaseType
    status: ReleaseStatus = ReleaseStatus.DRAFT
    release_date: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    release_notes: str = ""
    changelog: str = ""
    migration_guide: str = ""
    completed_task_ids: List[str] = field(default_factory=list)
    receipt_hashes: List[str] = field(default_factory=list)
    predecessor: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["release_type"] = self.release_type.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Release":
        if "release_type" in data and isinstance(data["release_type"], str):
            data["release_type"] = ReleaseType(data["release_type"])
        if "status" in data and isinstance(data["status"], str):
            data["status"] = ReleaseStatus(data["status"])
        return cls(**data)


class ReleasePipeline:
    """Manages the release lifecycle.

    Usage::

        pipeline = get_release_pipeline()
        release = await pipeline.create_release(
            version="3.3.0",
            release_type=ReleaseType.BETA,
            release_notes="...",
        )
        await pipeline.publish(release.version)
    """

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or _RELEASES_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._releases: Dict[str, Release] = {}  # version → Release
        self._load()

    def _load(self) -> None:
        """Load existing releases from disk."""
        for path in self.base_dir.glob("*.json"):
            try:
                with open(path) as f:
                    data = json.load(f)
                release = Release.from_dict(data)
                self._releases[release.version] = release
            except Exception as exc:
                logger.error(f"Failed to load release {path}: {exc}")

    def _persist(self, release: Release) -> None:
        """Persist a release to disk."""
        path = self.base_dir / f"v{release.version}.json"
        with open(path, "w") as f:
            json.dump(release.to_dict(), f, indent=2, default=str)

    async def create_release(
        self,
        version: str,
        release_type: ReleaseType,
        release_notes: str = "",
        changelog: str = "",
        migration_guide: str = "",
        completed_task_ids: Optional[List[str]] = None,
        receipt_hashes: Optional[List[str]] = None,
        predecessor: str = "",
    ) -> Release:
        """Create a new release (in DRAFT status)."""
        if version in self._releases:
            raise ValueError(f"Release {version} already exists")

        # Validate predecessor
        if predecessor and predecessor not in self._releases:
            raise ValueError(f"Predecessor {predecessor} not found")

        # Validate transition
        if predecessor:
            pred = self._releases[predecessor]
            if release_type not in VALID_TRANSITIONS.get(pred.release_type, []):
                raise ValueError(
                    f"Invalid transition: {pred.release_type.value} → {release_type.value}"
                )

        release = Release(
            version=version,
            release_type=release_type,
            release_notes=release_notes,
            changelog=changelog,
            migration_guide=migration_guide,
            completed_task_ids=completed_task_ids or [],
            receipt_hashes=receipt_hashes or [],
            predecessor=predecessor,
        )
        self._releases[version] = release
        self._persist(release)
        logger.info(f"Created release v{version} ({release_type.value})")
        return release

    async def publish(self, version: str) -> Optional[Release]:
        """Publish a draft release."""
        release = self._releases.get(version)
        if not release:
            return None
        if release.status != ReleaseStatus.DRAFT:
            return None
        release.status = ReleaseStatus.PUBLISHED
        release.release_date = datetime.now(timezone.utc).isoformat()
        self._persist(release)
        logger.info(f"Published release v{version}")
        return release

    async def yank(self, version: str, reason: str = "") -> Optional[Release]:
        """Yank a published release."""
        release = self._releases.get(version)
        if not release:
            return None
        release.status = ReleaseStatus.YANKED
        release.metadata["yank_reason"] = reason
        release.metadata["yank_date"] = datetime.now(timezone.utc).isoformat()
        self._persist(release)
        logger.warning(f"Yanked release v{version}: {reason}")
        return release

    async def get_release(self, version: str) -> Optional[Release]:
        return self._releases.get(version)

    async def list_releases(
        self,
        status: Optional[ReleaseStatus] = None,
        release_type: Optional[ReleaseType] = None,
    ) -> List[Release]:
        releases = list(self._releases.values())
        if status:
            releases = [r for r in releases if r.status == status]
        if release_type:
            releases = [r for r in releases if r.release_type == release_type]
        releases.sort(key=lambda r: r.release_date, reverse=True)
        return releases

    async def get_latest(self, release_type: Optional[ReleaseType] = None) -> Optional[Release]:
        releases = await self.list_releases(
            status=ReleaseStatus.PUBLISHED,
            release_type=release_type,
        )
        return releases[0] if releases else None

    async def generate_changelog(self, version: str) -> str:
        """Generate a changelog from completed tasks since the predecessor release."""
        release = self._releases.get(version)
        if not release:
            return ""

        predecessor_date = ""
        if release.predecessor and release.predecessor in self._releases:
            predecessor_date = self._releases[release.predecessor].release_date

        # Get all completed tasks after predecessor_date
        from core.task_system import get_task_queue, TaskStatus
        queue = get_task_queue()
        all_tasks = await queue.list_tasks(include_completed=True)
        relevant = [
            t for t in all_tasks
            if t.status == TaskStatus.COMPLETED
            and (not predecessor_date or t.completed_at > predecessor_date)
        ]

        lines = [f"# Changelog — v{version}", ""]
        if release.release_notes:
            lines.extend([release.release_notes, ""])

        # Group by phase
        by_phase: Dict[str, List] = {}
        for task in relevant:
            phase = task.phase.value
            if phase not in by_phase:
                by_phase[phase] = []
            by_phase[phase].append(task)

        for phase, tasks in sorted(by_phase.items()):
            lines.append(f"## {phase.replace('_', ' ').title()}")
            for task in tasks:
                lines.append(f"- {task.title}")
            lines.append("")

        changelog = "\n".join(lines)
        release.changelog = changelog
        self._persist(release)
        return changelog


# Singleton
_pipeline: Optional[ReleasePipeline] = None


def get_release_pipeline() -> ReleasePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = ReleasePipeline()
    return _pipeline
