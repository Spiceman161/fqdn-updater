from __future__ import annotations

from pathlib import Path

from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.infrastructure.config_repository import ConfigRepository


class ConfigBootstrapService:
    """Create an empty but valid application config."""

    def __init__(self, repository: ConfigRepository) -> None:
        self._repository = repository

    def create_default_config(self, path: Path) -> Path:
        config = AppConfig.default()
        self._repository.write_new(path=path, config=config)
        return path
