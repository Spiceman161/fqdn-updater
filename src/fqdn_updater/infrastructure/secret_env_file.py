from __future__ import annotations

import os
import re
import shlex
import tempfile
from pathlib import Path

_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_KEY_SAFE_CHAR_PATTERN = re.compile(r"[^A-Z0-9_]")


def password_env_key_for_router_id(router_id: str) -> str:
    normalized_router_id = _ENV_KEY_SAFE_CHAR_PATTERN.sub("_", router_id.upper()).strip("_")
    if not normalized_router_id:
        normalized_router_id = "ROUTER"
    return f"FQDN_UPDATER_ROUTER_{normalized_router_id}_PASSWORD"


class SecretEnvFile:
    """Read and update a simple KEY=value secret env file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}

        values: dict[str, str] = {}
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RuntimeError(f"Secret env file '{self._path}' could not be read: {exc}") from exc

        for line_number, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                raise RuntimeError(
                    f"Secret env file '{self._path}' line {line_number} must use KEY=value"
                )

            key, value = line.split("=", 1)
            key = key.strip()
            value = _strip_optional_quotes(value.strip())
            if not _ENV_KEY_PATTERN.fullmatch(key):
                raise RuntimeError(
                    f"Secret env file '{self._path}' line {line_number} has invalid key '{key}'"
                )
            if not value.strip():
                raise RuntimeError(
                    f"Secret env file '{self._path}' line {line_number} has blank value for '{key}'"
                )
            values[key] = value

        return values

    def write_value(self, *, key: str, value: str) -> None:
        if not _ENV_KEY_PATTERN.fullmatch(key):
            raise RuntimeError(f"Secret env key '{key}' is invalid")
        if not value.strip():
            raise RuntimeError(f"Secret env value for '{key}' must not be blank")

        values = self.read()
        values[key] = value
        self._atomic_write(values=values)

    def load_into_environment(self) -> None:
        for key, value in self.read().items():
            if key in os.environ:
                continue
            os.environ[key] = value

    def _atomic_write(self, *, values: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                for key in sorted(values):
                    handle.write(f"{key}={_format_env_value(values[key])}\n")
                temp_path = Path(handle.name)
            if temp_path is None:
                raise RuntimeError(f"Temporary secret env file was not created for {self._path}")
            _set_owner_only_permissions(path=temp_path)
            temp_path.replace(self._path)
            _set_owner_only_permissions(path=self._path)
        except OSError as exc:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Secret env file '{self._path}' could not be written: {exc}"
            ) from exc


def _strip_optional_quotes(value: str) -> str:
    try:
        parts = shlex.split(value, comments=False, posix=True)
    except ValueError as exc:
        raise RuntimeError(f"Secret env value could not be parsed: {exc}") from exc
    if not parts:
        return ""
    if len(parts) != 1:
        raise RuntimeError("Secret env value must be a single shell-style token")
    return parts[0]


def _format_env_value(value: str) -> str:
    if not value.strip():
        raise RuntimeError("Secret env values must not be blank")
    return shlex.quote(value)


def _set_owner_only_permissions(*, path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        return
