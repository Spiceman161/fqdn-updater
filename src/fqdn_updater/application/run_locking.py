from __future__ import annotations

from contextlib import nullcontext
from typing import Protocol

from fqdn_updater.domain.config_schema import AppConfig


class RunLockManager(Protocol):
    def acquire(self, *, config: AppConfig):
        """Return a context manager that holds the run lock."""


class NullRunLockManager:
    def acquire(self, *, config: AppConfig):
        del config
        return nullcontext()
