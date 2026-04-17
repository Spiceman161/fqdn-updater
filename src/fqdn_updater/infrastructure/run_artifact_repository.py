from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.run_artifact import RunArtifact


@dataclass(frozen=True)
class RunArtifactReadWarning:
    path: Path
    message: str


@dataclass(frozen=True)
class StoredRunArtifact:
    path: Path
    artifact: RunArtifact


@dataclass(frozen=True)
class RunArtifactListResult:
    artifacts: tuple[StoredRunArtifact, ...]
    warnings: tuple[RunArtifactReadWarning, ...]


class RunArtifactRepository:
    """Persist machine-readable run artifacts as JSON."""

    def write(self, config: AppConfig, artifact: RunArtifact) -> Path:
        artifacts_dir = Path(config.runtime.artifacts_dir)
        target_path = artifacts_dir / f"{artifact.run_id}.json"
        self._atomic_write(path=target_path, payload=artifact.model_dump(mode="json"))
        return target_path

    def list_recent(self, *, artifacts_dir: Path, limit: int = 10) -> RunArtifactListResult:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        if not artifacts_dir.exists():
            return RunArtifactListResult(artifacts=(), warnings=())
        if not artifacts_dir.is_dir():
            return RunArtifactListResult(
                artifacts=(),
                warnings=(
                    RunArtifactReadWarning(
                        path=artifacts_dir,
                        message="artifacts path is not a directory",
                    ),
                ),
            )

        artifacts: list[StoredRunArtifact] = []
        warnings: list[RunArtifactReadWarning] = []
        for path in sorted(artifacts_dir.glob("*.json"), key=lambda item: item.name):
            try:
                artifact = self.read(path=path)
            except RuntimeError as exc:
                warnings.append(RunArtifactReadWarning(path=path, message=str(exc)))
                continue
            artifacts.append(StoredRunArtifact(path=path, artifact=artifact))

        artifacts.sort(key=_artifact_sort_key, reverse=True)
        warnings.sort(key=lambda warning: str(warning.path))
        return RunArtifactListResult(
            artifacts=tuple(artifacts[:limit]),
            warnings=tuple(warnings),
        )

    def read(self, *, path: Path) -> RunArtifact:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RuntimeError(f"could not read artifact: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"artifact JSON is invalid: {exc.msg}") from exc

        try:
            return RunArtifact.model_validate(payload)
        except ValidationError as exc:
            first_error = exc.errors()[0] if exc.errors() else {}
            location = ".".join(str(part) for part in first_error.get("loc", ()))
            message = str(first_error.get("msg", "artifact schema is invalid"))
            detail = f"{location}: {message}" if location else message
            raise RuntimeError(f"artifact schema is invalid: {detail}") from exc

    def _atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                temp_path = Path(handle.name)
            if temp_path is None:
                raise RuntimeError(f"Temporary file was not created for {path}")
            temp_path.replace(path)
        except OSError as exc:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise RuntimeError(f"Failed to write run artifact {path}: {exc}") from exc


def _artifact_sort_key(artifact: StoredRunArtifact) -> tuple[float, str, str]:
    finished_at = artifact.artifact.finished_at
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=UTC)
    return (
        finished_at.astimezone(UTC).timestamp(),
        artifact.artifact.run_id,
        artifact.path.name,
    )
