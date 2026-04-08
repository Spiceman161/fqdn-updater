from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from fqdn_updater.domain.config_schema import AppConfig


class ConfigRepository:
    """Persist and load application config as JSON."""

    def load(self, path: Path) -> AppConfig:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON in config file {path}: {exc}") from exc
        except OSError as exc:
            raise RuntimeError(f"Failed to read config file {path}: {exc}") from exc

        try:
            return AppConfig.model_validate(payload)
        except ValidationError as exc:
            raise RuntimeError(self._format_validation_error(path=path, exc=exc)) from exc

    def write_new(self, path: Path, config: AppConfig) -> None:
        if path.exists():
            raise RuntimeError(f"Config file already exists: {path}")
        self._atomic_write(path=path, payload=config.model_dump(mode="json"))

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
            raise RuntimeError(f"Failed to write config file {path}: {exc}") from exc

    def _format_validation_error(self, path: Path, exc: ValidationError) -> str:
        formatted_errors = [f"Config validation failed for {path}:"]
        for error in exc.errors(include_url=False):
            location = self._format_location(error.get("loc", ()))
            message = self._format_issue_message(error.get("msg", "validation error"))
            formatted_errors.append(f"- {location}: {message}")
        return "\n".join(formatted_errors)

    def _format_location(self, location: tuple[Any, ...]) -> str:
        if not location:
            return "config"

        parts: list[str] = []
        for item in location:
            if isinstance(item, int):
                if parts:
                    parts[-1] = f"{parts[-1]}[{item}]"
                else:
                    parts.append(f"[{item}]")
                continue
            parts.append(str(item))
        return ".".join(parts)

    def _format_issue_message(self, message: str) -> str:
        prefix = "Value error, "
        if message.startswith(prefix):
            return message[len(prefix) :]
        return message
