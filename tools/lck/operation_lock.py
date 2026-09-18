from __future__ import annotations

import fcntl
import os
from dataclasses import dataclass
from pathlib import Path

from .models import LckStopError


@dataclass
class TaskOperationLock:
    """Serialize lifecycle operations for one Task within this repository.

    The lock is intentionally process-scoped and contains no lifecycle authority.
    Durable handoffs such as Review and Remediation keep their existing markers;
    taking this lock makes checking or creating those markers atomic with respect
    to every other LCK CLI operation for the same Task.
    """

    path: Path
    task_number: int
    operation: str
    _fd: int = -1

    @classmethod
    def acquire(
        cls, repo_root: Path, task_number: int, operation: str
    ) -> TaskOperationLock:
        root = repo_root / ".workflow.local" / "lck" / "task-operation-locks"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"task-{task_number}.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(fd)
            raise LckStopError(
                f"Task #{task_number} already has an active LCK operation"
            ) from exc
        return cls(
            path=path,
            task_number=task_number,
            operation=operation,
            _fd=fd,
        )

    def release(self) -> None:
        if self._fd < 0:
            return
        fcntl.flock(self._fd, fcntl.LOCK_UN)
        os.close(self._fd)
        self._fd = -1
