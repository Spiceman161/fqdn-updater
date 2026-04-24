from __future__ import annotations

import fcntl
import os
from pathlib import Path

from fqdn_updater.domain.config_schema import AppConfig


class RunLockError(RuntimeError):
    """Raised when another sync or dry-run already holds the runtime lock."""


class FileRunLock:
    def __init__(self, *, path: Path) -> None:
        self._path = path
        self._handle = None

    def __enter__(self) -> FileRunLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            self._handle = None
            raise RunLockError(
                f"Another run is already in progress (lock file: {self._path})"
            ) from exc

        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(str(os.getpid()))
        self._handle.flush()
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        if self._handle is None:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


class FileRunLockManager:
    def acquire(self, *, config: AppConfig) -> FileRunLock:
        return FileRunLock(path=Path(config.runtime.state_dir) / "run.lock")
