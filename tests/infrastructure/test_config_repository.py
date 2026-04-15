from __future__ import annotations

import json
from pathlib import Path

import pytest

from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.infrastructure.config_repository import ConfigRepository


def test_atomic_write_cleans_up_temp_file_on_replace_failure(tmp_path, monkeypatch) -> None:
    repository = ConfigRepository()
    target_path = tmp_path / "config.json"

    def failing_replace(self: Path, target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", failing_replace)

    with pytest.raises(RuntimeError, match="Failed to write config file"):
        repository.write_new(path=target_path, config=AppConfig.default())

    assert not target_path.exists()
    temp_files = list(tmp_path.glob(".config.json.*.tmp"))
    assert temp_files == []


def test_overwrite_uses_atomic_replace_path(tmp_path, monkeypatch) -> None:
    repository = ConfigRepository()
    target_path = tmp_path / "config.json"
    target_path.write_text("{}", encoding="utf-8")
    config = AppConfig.default()
    replace_calls: list[tuple[Path, Path]] = []
    original_replace = Path.replace

    def recording_replace(self: Path, target: Path) -> Path:
        replace_calls.append((self, target))
        assert self.parent == target.parent
        assert self.name.startswith(f".{target.name}.")
        assert self.suffix == ".tmp"
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", recording_replace)

    repository.overwrite(path=target_path, config=config)

    assert replace_calls
    assert json.loads(target_path.read_text(encoding="utf-8")) == config.model_dump(mode="json")


def test_overwrite_refuses_missing_config(tmp_path) -> None:
    repository = ConfigRepository()
    target_path = tmp_path / "missing.json"

    with pytest.raises(RuntimeError, match="Config file does not exist"):
        repository.overwrite(path=target_path, config=AppConfig.default())

    assert not target_path.exists()
