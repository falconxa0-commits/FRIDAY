"""Persistent Engineering Task System — durable work management.

This module implements a durable task system that survives process
restarts, supports dependencies between tasks, tracks execution
history, and produces cryptographic receipts for completed work.

Design principles:
    - **Durable**: All task state is persisted to disk (JSON).
    - **Resumable**: Interrupted tasks can be resumed from checkpoints.
    - **Dependency-aware**: Tasks block on their dependencies.
    - **Receipt-generating**: Every completed task produces a signed
      receipt (HMAC-SHA256) that can be independently verified.
    - **Backward-compatible**: Does not modify any existing modules.

Persistence layout::

    .friday/
    ├── tasks/
    │   ├── queue.json          # Active + pending tasks
    │   ├── completed.json      # Finished tasks (history)
    │   ├── blocked.json        # Tasks waiting on dependencies
    │   └── receipts/           # One receipt file per completed task
    └── ...

Task lifecycle::

    PENDING → READY → IN_PROGRESS → COMPLETED
                ↘ BLOCKED (dependency not met)
                ↘ FAILED (retries exhausted)
                ↘ CANCELLED
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("friday.task_system")

# ---------------------------------------------------------------------------
# Persistence paths
# ---------------------------------------------------------------------------
_FRIDAY_ROOT = Path(os.environ.get(
    "FRIDAY_ENGINEERING_DIR",
    str(Path.cwd() / ".friday"),
))
_TASKS_DIR = _FRIDAY_ROOT / "tasks"
_QUEUE_PATH = _TASKS_DIR / "queue.json"
_COMPLETED_PATH = _TASKS_DIR / "completed.json"
_BLOCKED_PATH = _TASKS_DIR / "blocked.json"
_RECEIPTS_DIR = _TASKS_DIR / "receipts"

# Ensure directories exist
for _p in (_TASKS_DIR, _RECEIPTS_DIR):
    _p.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    BLOCKED = "blocked"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def weight(self) -> int:
        return {"critical": 0, "high": 1, "medium": 2, "low": 3}[self.value]


class TaskPhase(str, Enum):
    """Which engineering phase owns this task."""
    ORGANIZATION = "organization"
    WORK_MANAGEMENT = "work_management"
    KNOWLEDGE = "knowledge"
    VALIDATION = "validation"
    DX = "developer_experience"
    RELEASE = "release_pipeline"
    RESEARCH = "research"
    PERFORMANCE = "performance"
    SECURITY = "security"
    QUALITY = "quality"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class TaskCheckpoint:
    """A checkpoint in a task's execution — for resumability."""
    name: str
    timestamp: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskHistoryEntry:
    """A single entry in a task's execution history."""
    timestamp: str
    event: str
    actor: str  # agent name or "human"
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    """A durable engineering task.

    Attributes:
        id: Unique task ID (UUID).
        title: Short human-readable title.
        description: Detailed description of what needs to be done.
        phase: Engineering phase that owns this task.
        priority: Critical/High/Medium/Low.
        status: Current lifecycle status.
        dependencies: List of task IDs that must complete before this one.
        blockers: List of human-readable blocker descriptions.
        assigned_to: Agent role or human assigned to the task.
        max_retries: Maximum retry count (default 3).
        retry_count: Current retry count.
        checkpoints: List of checkpoints for resumable execution.
        history: Chronological list of events.
        created_at: ISO timestamp.
        updated_at: ISO timestamp.
        completed_at: ISO timestamp (None if not completed).
        receipt_hash: HMAC-SHA256 receipt hash (None until completed).
        metadata: Arbitrary key-value pairs for task-specific data.
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    description: str = ""
    phase: TaskPhase = TaskPhase.QUALITY
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    dependencies: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    assigned_to: str = ""
    max_retries: int = 3
    retry_count: int = 0
    checkpoints: List[TaskCheckpoint] = field(default_factory=list)
    history: List[TaskHistoryEntry] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
    receipt_hash: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        # Handle enum conversions
        if "phase" in data and isinstance(data["phase"], str):
            data["phase"] = TaskPhase(data["phase"])
        if "priority" in data and isinstance(data["priority"], str):
            data["priority"] = TaskPriority(data["priority"])
        if "status" in data and isinstance(data["status"], str):
            data["status"] = TaskStatus(data["status"])
        # Handle nested dataclasses
        if "checkpoints" in data:
            data["checkpoints"] = [
                TaskCheckpoint(**cp) if isinstance(cp, dict) else cp
                for cp in data["checkpoints"]
            ]
        if "history" in data:
            data["history"] = [
                TaskHistoryEntry(**he) if isinstance(he, dict) else he
                for he in data["history"]
            ]
        return cls(**data)

    def add_history(self, event: str, actor: str = "system", **details) -> None:
        """Append a history entry."""
        self.history.append(TaskHistoryEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event=event,
            actor=actor,
            details=details,
        ))
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def add_checkpoint(self, name: str, **data) -> None:
        """Add a checkpoint for resumable execution."""
        self.checkpoints.append(TaskCheckpoint(
            name=name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            data=data,
        ))
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.add_history("checkpoint_added", details={"checkpoint": name})


@dataclass
class TaskReceipt:
    """Cryptographic receipt proving a task was completed.

    The receipt is HMAC-SHA256 signed with the same secret as the
    audit ledger, so receipts are independently verifiable by anyone
    with access to the secret.
    """
    task_id: str
    title: str
    phase: str
    completed_at: str
    duration_seconds: float
    tests_passed: int
    tests_failed: int
    hash: str  # HMAC-SHA256 of the above fields
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskReceipt":
        return cls(**data)


# ---------------------------------------------------------------------------
# Receipt signing (reuses the ledger HMAC secret)
# ---------------------------------------------------------------------------
def _get_hmac_secret() -> bytes:
    """Get the HMAC secret (shared with the audit ledger)."""
    try:
        from core.ledger import ActionLedger
        return ActionLedger._get_hmac_secret()
    except Exception:
        # Fallback: generate a stable per-install secret
        import getpass
        import socket
        return f"friday-tasks-{socket.gethostname()}-{getpass.getuser()}".encode()


def _sign_receipt(receipt_data: Dict[str, Any]) -> str:
    """Sign a receipt with HMAC-SHA256."""
    content = json.dumps(receipt_data, sort_keys=True, default=str)
    return hmac.new(_get_hmac_secret(), content.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_receipt(receipt: TaskReceipt) -> bool:
    """Verify that a receipt's signature is valid."""
    expected = _sign_receipt({
        "task_id": receipt.task_id,
        "title": receipt.title,
        "phase": receipt.phase,
        "completed_at": receipt.completed_at,
        "duration_seconds": receipt.duration_seconds,
        "tests_passed": receipt.tests_passed,
        "tests_failed": receipt.tests_failed,
        "metadata": receipt.metadata,
    })
    return hmac.compare_digest(expected, receipt.hash)


# ---------------------------------------------------------------------------
# Task Queue — the persistent store
# ---------------------------------------------------------------------------
class TaskQueue:
    """Persistent, dependency-aware task queue.

    All state is persisted to JSON files in ``.friday/tasks/``.
    The queue is safe for concurrent access within a single process
    (uses an asyncio.Lock). For multi-process access, use the API
    layer which serializes through the FastAPI server.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir is None:
            base_dir = _TASKS_DIR
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.queue_path = self.base_dir / "queue.json"
        self.completed_path = self.base_dir / "completed.json"
        self.blocked_path = self.base_dir / "blocked.json"
        self.receipts_dir = self.base_dir / "receipts"
        self.receipts_dir.mkdir(exist_ok=True)

        self._lock = asyncio.Lock()
        self._tasks: Dict[str, Task] = {}  # id → Task (active)
        self._completed: Dict[str, Task] = {}  # id → Task (completed)
        self._load()

    def _load(self) -> None:
        """Load tasks from disk."""
        if self.queue_path.exists():
            try:
                with open(self.queue_path) as f:
                    data = json.load(f)
                for task_data in data:
                    task = Task.from_dict(task_data)
                    self._tasks[task.id] = task
                logger.info(f"Loaded {len(self._tasks)} active tasks from {self.queue_path}")
            except Exception as exc:
                logger.error(f"Failed to load task queue: {exc}")

        if self.completed_path.exists():
            try:
                with open(self.completed_path) as f:
                    data = json.load(f)
                for task_data in data:
                    task = Task.from_dict(task_data)
                    self._completed[task.id] = task
                logger.info(f"Loaded {len(self._completed)} completed tasks")
            except Exception as exc:
                logger.error(f"Failed to load completed tasks: {exc}")

    def _persist_active(self) -> None:
        """Persist active tasks to disk."""
        data = [t.to_dict() for t in self._tasks.values()]
        with open(self.queue_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _persist_completed(self) -> None:
        """Persist completed tasks to disk."""
        data = [t.to_dict() for t in self._completed.values()]
        with open(self.completed_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def _persist_receipt(self, receipt: TaskReceipt) -> None:
        """Persist a task receipt to disk."""
        path = self.receipts_dir / f"{receipt.task_id}.json"
        with open(path, "w") as f:
            json.dump(receipt.to_dict(), f, indent=2, default=str)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_task(
        self,
        title: str,
        description: str = "",
        phase: TaskPhase = TaskPhase.QUALITY,
        priority: TaskPriority = TaskPriority.MEDIUM,
        dependencies: Optional[List[str]] = None,
        assigned_to: str = "",
        max_retries: int = 3,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """Create a new task and persist it."""
        async with self._lock:
            task = Task(
                title=title,
                description=description,
                phase=phase,
                priority=priority,
                dependencies=dependencies or [],
                assigned_to=assigned_to,
                max_retries=max_retries,
                metadata=metadata or {},
            )
            task.add_history("created", details={"priority": priority.value})
            self._tasks[task.id] = task
            self._persist_active()
            logger.info(f"Created task {task.id[:8]}: {title}")
            return task

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID (checks active then completed)."""
        async with self._lock:
            if task_id in self._tasks:
                return self._tasks[task_id]
            if task_id in self._completed:
                return self._completed[task_id]
            return None

    async def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        phase: Optional[TaskPhase] = None,
        priority: Optional[TaskPriority] = None,
        include_completed: bool = False,
    ) -> List[Task]:
        """List tasks, optionally filtered."""
        async with self._lock:
            tasks = list(self._tasks.values())
            if include_completed:
                tasks.extend(self._completed.values())

            if status:
                tasks = [t for t in tasks if t.status == status]
            if phase:
                tasks = [t for t in tasks if t.phase == phase]
            if priority:
                tasks = [t for t in tasks if t.priority == priority]

            # Sort by priority weight, then by created_at
            tasks.sort(key=lambda t: (t.priority.weight, t.created_at))
            return tasks

    async def start_task(self, task_id: str, actor: str = "system") -> Optional[Task]:
        """Mark a task as in-progress. Returns the task or None if not startable."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            # Check dependencies
            blocked_by = []
            for dep_id in task.dependencies:
                dep = self._completed.get(dep_id) or self._tasks.get(dep_id)
                if not dep or dep.status != TaskStatus.COMPLETED:
                    blocked_by.append(dep_id)

            if blocked_by:
                task.status = TaskStatus.BLOCKED
                task.blockers = [f"Waiting on task {bid[:8]}" for bid in blocked_by]
                task.add_history("blocked", actor=actor, details={"blocked_by": blocked_by})
                self._persist_active()
                logger.warning(f"Task {task_id[:8]} blocked by {len(blocked_by)} dependencies")
                return task

            # Check retry limit
            if task.retry_count >= task.max_retries:
                task.status = TaskStatus.FAILED
                task.add_history("failed_max_retries", actor=actor,
                                 details={"retries": task.retry_count})
                self._persist_active()
                logger.error(f"Task {task_id[:8]} failed: max retries ({task.max_retries}) exceeded")
                return task

            task.status = TaskStatus.IN_PROGRESS
            task.retry_count += 1
            task.add_history("started", actor=actor)
            self._persist_active()
            logger.info(f"Started task {task_id[:8]} (attempt {task.retry_count})")
            return task

    async def complete_task(
        self,
        task_id: str,
        actor: str = "system",
        tests_passed: int = 0,
        tests_failed: int = 0,
        duration_seconds: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[TaskReceipt]:
        """Mark a task as completed and generate a receipt."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc).isoformat()
            task.add_history("completed", actor=actor,
                             details={"tests_passed": tests_passed,
                                      "tests_failed": tests_failed})

            # Generate receipt
            receipt = TaskReceipt(
                task_id=task.id,
                title=task.title,
                phase=task.phase.value,
                completed_at=task.completed_at,
                duration_seconds=duration_seconds,
                tests_passed=tests_passed,
                tests_failed=tests_failed,
                hash="",  # filled below
                metadata=metadata or {},
            )
            receipt.hash = _sign_receipt({
                "task_id": receipt.task_id,
                "title": receipt.title,
                "phase": receipt.phase,
                "completed_at": receipt.completed_at,
                "duration_seconds": receipt.duration_seconds,
                "tests_passed": receipt.tests_passed,
                "tests_failed": receipt.tests_failed,
                "metadata": receipt.metadata,
            })
            task.receipt_hash = receipt.hash

            # Move to completed
            self._completed[task.id] = task
            del self._tasks[task.id]

            self._persist_active()
            self._persist_completed()
            self._persist_receipt(receipt)

            logger.info(f"Completed task {task_id[:8]}: {task.title}")
            return receipt

    async def fail_task(
        self,
        task_id: str,
        actor: str = "system",
        error: str = "",
        can_retry: bool = True,
    ) -> Optional[Task]:
        """Mark a task as failed. If can_retry and retries remain, re-queue."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            task.add_history("failed", actor=actor, details={"error": error})

            if can_retry and task.retry_count < task.max_retries:
                task.status = TaskStatus.PENDING
                task.add_history("requeued", actor=actor,
                                 details={"retry": task.retry_count + 1})
                logger.info(f"Task {task_id[:8]} re-queued for retry")
            else:
                task.status = TaskStatus.FAILED
                logger.error(f"Task {task_id[:8]} permanently failed: {error}")

            self._persist_active()
            return task

    async def cancel_task(self, task_id: str, actor: str = "human") -> Optional[Task]:
        """Cancel a task."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task.status = TaskStatus.CANCELLED
            task.add_history("cancelled", actor=actor)
            self._persist_active()
            logger.info(f"Cancelled task {task_id[:8]}")
            return task

    async def add_dependency(self, task_id: str, depends_on_id: str) -> bool:
        """Add a dependency to a task."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            if depends_on_id not in task.dependencies:
                task.dependencies.append(depends_on_id)
                task.add_history("dependency_added",
                                 details={"depends_on": depends_on_id})
                self._persist_active()
            return True

    async def add_checkpoint(self, task_id: str, name: str, **data) -> bool:
        """Add a checkpoint to a task for resumable execution."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            task.add_checkpoint(name, **data)
            self._persist_active()
            return True

    async def get_ready_tasks(self) -> List[Task]:
        """Get all tasks that are ready to execute (dependencies met)."""
        async with self._lock:
            ready = []
            for task in self._tasks.values():
                if task.status not in (TaskStatus.PENDING, TaskStatus.BLOCKED):
                    continue
                blocked = False
                for dep_id in task.dependencies:
                    dep = self._completed.get(dep_id)
                    if not dep:
                        blocked = True
                        break
                if not blocked:
                    ready.append(task)
            ready.sort(key=lambda t: (t.priority.weight, t.created_at))
            return ready

    async def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        async with self._lock:
            active = list(self._tasks.values())
            completed = list(self._completed.values())
            return {
                "active": len(active),
                "completed": len(completed),
                "by_status": {
                    s.value: sum(1 for t in active if t.status == s)
                    for s in TaskStatus
                },
                "by_priority": {
                    p.value: sum(1 for t in active if t.priority == p)
                    for p in TaskPriority
                },
                "by_phase": {
                    ph.value: sum(1 for t in active if t.phase == ph)
                    for ph in TaskPhase
                },
                "receipts": len(list(self.receipts_dir.glob("*.json"))),
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_queue: Optional[TaskQueue] = None


def get_task_queue() -> TaskQueue:
    """Get the singleton TaskQueue instance."""
    global _queue
    if _queue is None:
        _queue = TaskQueue()
    return _queue
