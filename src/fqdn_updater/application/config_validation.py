from __future__ import annotations

from pathlib import Path

from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.infrastructure.config_repository import ConfigRepository


class ConfigValidationService:
    """Load and validate a persisted application config."""

    def __init__(self, repository: ConfigRepository) -> None:
        self._repository = repository

    def validate(self, path: Path) -> AppConfig:
        return self._repository.load(path=path)
