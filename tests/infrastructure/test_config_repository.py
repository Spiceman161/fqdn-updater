from __future__ import annotations

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
