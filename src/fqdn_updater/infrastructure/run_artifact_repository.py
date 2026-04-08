from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.run_artifact import RunArtifact


class RunArtifactRepository:
    """Persist machine-readable run artifacts as JSON."""

    def write(self, config: AppConfig, artifact: RunArtifact) -> Path:
        artifacts_dir = Path(config.runtime.artifacts_dir)
        target_path = artifacts_dir / f"{artifact.run_id}.json"
        self._atomic_write(path=target_path, payload=artifact.model_dump(mode="json"))
        return target_path

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
