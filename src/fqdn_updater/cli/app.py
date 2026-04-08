from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import typer

from fqdn_updater.application.config_bootstrap import ConfigBootstrapService
from fqdn_updater.application.config_validation import ConfigValidationService
from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.infrastructure.config_repository import ConfigRepository

app = typer.Typer(help="Synchronize managed FQDN object-groups on Keenetic routers.")
config_app = typer.Typer(help="Configuration management commands.")
app.add_typer(config_app, name="config")

DEFAULT_CONFIG_PATH = Path("config.json")
INIT_CONFIG_OPTION = typer.Option(
    DEFAULT_CONFIG_PATH,
    "--config",
    dir_okay=False,
    help="Path to the JSON config file to create.",
)
VALIDATE_CONFIG_OPTION = typer.Option(
    DEFAULT_CONFIG_PATH,
    "--config",
    exists=True,
    dir_okay=False,
    readable=True,
    help="Path to the JSON config file to validate.",
)


def _repository() -> ConfigRepository:
    return ConfigRepository()


def _bootstrap_service() -> ConfigBootstrapService:
    return ConfigBootstrapService(repository=_repository())


def _validation_service() -> ConfigValidationService:
    return ConfigValidationService(repository=_repository())


@app.command("init")
def init_command(config: Path = INIT_CONFIG_OPTION) -> None:
    """Create a new scaffold config file."""
    try:
        created_path = _bootstrap_service().create_default_config(path=config)
    except RuntimeError as exc:
        _runtime_error_handler(exc)
    typer.echo(f"Created scaffold config: {created_path}")


@config_app.command("validate")
def validate_command(config: Path = VALIDATE_CONFIG_OPTION) -> None:
    """Validate an existing config file."""
    try:
        validated_config = _validation_service().validate(path=config)
    except RuntimeError as exc:
        _runtime_error_handler(exc)
    _render_validation_success(config=validated_config, path=config)


def _render_validation_success(config: AppConfig, path: Path) -> None:
    typer.echo(
        "Config is valid: "
        f"path={path} version={config.version} routers={len(config.routers)} "
        f"services={len(config.services)} mappings={len(config.mappings)}"
    )


def _runtime_error_handler(exc: RuntimeError) -> NoReturn:
    typer.echo(str(exc), err=True)
    raise typer.Exit(code=1) from exc


def main() -> None:
    app()
