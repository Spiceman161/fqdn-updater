from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.run_artifact import RunArtifact


@dataclass(frozen=True)
class RunHistoryWarning:
    path: Path
    message: str


@dataclass(frozen=True)
class RecentRun:
    path: Path
    artifact: RunArtifact


@dataclass(frozen=True)
class RunHistoryResult:
    artifacts_dir: Path
    runs: tuple[RecentRun, ...]
    warnings: tuple[RunHistoryWarning, ...]


class StoredRunArtifact(Protocol):
    path: Path
    artifact: RunArtifact


class RunArtifactReadWarning(Protocol):
    path: Path
    message: str


class RunArtifactListResult(Protocol):
    artifacts: tuple[StoredRunArtifact, ...]
    warnings: tuple[RunArtifactReadWarning, ...]


class RunArtifactReader(Protocol):
    def list_recent(self, *, artifacts_dir: Path, limit: int = 10) -> RunArtifactListResult:
        """Read recent local run artifacts from an artifacts directory."""


class RunHistoryService:
    def __init__(self, *, repository: RunArtifactReader) -> None:
        self._repository = repository

    def list_recent(
        self,
        *,
        config: AppConfig,
        config_path: Path,
        limit: int = 10,
    ) -> RunHistoryResult:
        artifacts_dir = _resolve_config_relative_path(
            config_path=config_path,
            configured_path=config.runtime.artifacts_dir,
        )
        result = self._repository.list_recent(artifacts_dir=artifacts_dir, limit=limit)
        return RunHistoryResult(
            artifacts_dir=artifacts_dir,
            runs=tuple(
                RecentRun(path=item.path, artifact=item.artifact) for item in result.artifacts
            ),
            warnings=tuple(
                RunHistoryWarning(path=warning.path, message=warning.message)
                for warning in result.warnings
            ),
        )


def _resolve_config_relative_path(*, config_path: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    if path.is_absolute():
        return path
    return config_path.parent / path
