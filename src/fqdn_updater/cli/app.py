from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import NoReturn

import typer

from fqdn_updater.application.config_bootstrap import ConfigBootstrapService
from fqdn_updater.application.config_validation import ConfigValidationService
from fqdn_updater.application.dry_run_orchestration import DryRunExecutionResult, DryRunOrchestrator
from fqdn_updater.application.service_sync_planning import ServiceSyncPlan, ServiceSyncPlanner
from fqdn_updater.application.source_loading import SourceLoadingService
from fqdn_updater.application.sync_orchestration import SyncExecutionResult, SyncOrchestrator
from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.run_artifact import RunStatus, RunTrigger
from fqdn_updater.infrastructure.config_repository import ConfigRepository
from fqdn_updater.infrastructure.keenetic_rci_client import KeeneticRciClientFactory
from fqdn_updater.infrastructure.raw_source_fetcher import HttpRawSourceFetcher
from fqdn_updater.infrastructure.router_secret_resolver import EnvironmentFileSecretResolver
from fqdn_updater.infrastructure.run_artifact_repository import RunArtifactRepository
from fqdn_updater.infrastructure.run_logging import RunLoggerFactory

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
DRY_RUN_CONFIG_OPTION = typer.Option(
    DEFAULT_CONFIG_PATH,
    "--config",
    dir_okay=False,
    help="Path to the JSON config file to use for the dry-run.",
)
SYNC_CONFIG_OPTION = typer.Option(
    DEFAULT_CONFIG_PATH,
    "--config",
    dir_okay=False,
    help="Path to the JSON config file to use for sync.",
)


class OutputMode(StrEnum):
    HUMAN = "human"
    JSON = "json"


DRY_RUN_OUTPUT_OPTION = typer.Option(
    OutputMode.HUMAN,
    "--output",
    case_sensitive=False,
    help="Output format for dry-run results.",
)


def _repository() -> ConfigRepository:
    return ConfigRepository()


def _bootstrap_service() -> ConfigBootstrapService:
    return ConfigBootstrapService(repository=_repository())


def _validation_service() -> ConfigValidationService:
    return ConfigValidationService(repository=_repository())


def _dry_run_orchestrator() -> DryRunOrchestrator:
    return DryRunOrchestrator(
        source_loader=SourceLoadingService(fetcher=HttpRawSourceFetcher()),
        secret_resolver=EnvironmentFileSecretResolver(),
        client_factory=KeeneticRciClientFactory(),
        planner=ServiceSyncPlanner(),
        artifact_writer=RunArtifactRepository(),
        logger_factory=RunLoggerFactory(),
    )


def _sync_orchestrator() -> SyncOrchestrator:
    return SyncOrchestrator(
        source_loader=SourceLoadingService(fetcher=HttpRawSourceFetcher()),
        secret_resolver=EnvironmentFileSecretResolver(),
        client_factory=KeeneticRciClientFactory(),
        planner=ServiceSyncPlanner(),
        artifact_writer=RunArtifactRepository(),
        logger_factory=RunLoggerFactory(),
    )


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


@app.command("dry-run")
def dry_run_command(
    config: Path = DRY_RUN_CONFIG_OPTION,
    output: OutputMode = DRY_RUN_OUTPUT_OPTION,
) -> None:
    """Run a read-only sync preview against configured routers."""
    try:
        validated_config = _validation_service().validate(path=config)
    except RuntimeError as exc:
        _runtime_error_handler(exc, code=40)

    try:
        result = _dry_run_orchestrator().run(
            config=validated_config,
            trigger=RunTrigger.MANUAL,
        )
    except RuntimeError as exc:
        _runtime_error_handler(exc, code=20)

    if output is OutputMode.JSON:
        typer.echo(_render_dry_run_json(result=result))
    else:
        typer.echo(_render_dry_run_human(result=result))
    raise typer.Exit(code=_dry_run_exit_code(result=result))


@app.command("sync")
def sync_command(
    config: Path = SYNC_CONFIG_OPTION,
    output: OutputMode = DRY_RUN_OUTPUT_OPTION,
) -> None:
    """Apply managed object-group changes against configured routers."""
    try:
        validated_config = _validation_service().validate(path=config)
    except RuntimeError as exc:
        _runtime_error_handler(exc, code=40)

    try:
        result = _sync_orchestrator().run(
            config=validated_config,
            trigger=RunTrigger.MANUAL,
        )
    except RuntimeError as exc:
        _runtime_error_handler(exc, code=20)

    if output is OutputMode.JSON:
        typer.echo(_render_sync_json(result=result))
    else:
        typer.echo(_render_sync_human(result=result))
    raise typer.Exit(code=_sync_exit_code(result=result))


def _render_validation_success(config: AppConfig, path: Path) -> None:
    typer.echo(
        "Config is valid: "
        f"path={path} version={config.version} routers={len(config.routers)} "
        f"services={len(config.services)} mappings={len(config.mappings)}"
    )


def _render_dry_run_human(result: DryRunExecutionResult) -> str:
    return _render_operation_human(
        operation_name="Dry run completed",
        result=result,
        include_diff_details=True,
    )


def _render_sync_human(result: SyncExecutionResult) -> str:
    return _render_operation_human(
        operation_name="Sync completed",
        result=result,
        include_diff_details=False,
    )


def _render_operation_human(
    *,
    operation_name: str,
    result: DryRunExecutionResult | SyncExecutionResult,
    include_diff_details: bool,
) -> str:
    artifact = result.artifact
    failed_services = sum(
        service.error_message is not None
        for router in artifact.router_results
        for service in router.service_results
    )
    skipped_services = sum(
        service.status.value == "skipped"
        for router in artifact.router_results
        for service in router.service_results
    )
    changed_services = sum(plan.has_changes for plan in result.plans)
    plan_index = {
        (plan.router_id, plan.service_key, plan.object_group_name): plan for plan in result.plans
    }

    lines = [
        f"{operation_name}: "
        f"run_id={artifact.run_id} status={artifact.status.value} "
        f"artifact_path={result.artifact_path} routers={len(artifact.router_results)} "
        f"planned_changes={changed_services} failed_services={failed_services} "
        f"skipped_services={skipped_services}"
    ]

    for router in artifact.router_results:
        lines.append(f"Router {router.router_id}: status={router.status.value}")
        for service in router.service_results:
            lines.append(
                "  "
                f"Service {service.service_key} group={service.object_group_name} "
                f"status={service.status.value} added={service.added_count} "
                f"removed={service.removed_count} unchanged={service.unchanged_count} "
                f"route_changed={'yes' if service.route_changed else 'no'}"
            )
            if service.error_message is not None:
                lines.append(f"    error: {service.error_message}")
                continue

            plan = plan_index.get(
                (router.router_id, service.service_key, service.object_group_name)
            )
            if not include_diff_details or plan is None or not plan.has_changes:
                continue

            diff = plan.object_group_diff
            route_diff = plan.route_binding_diff
            lines.append(f"    needs_create: {str(diff.needs_create).lower()}")
            lines.append(f"    to_add: {_format_entries(diff.to_add)}")
            lines.append(f"    to_remove: {_format_entries(diff.to_remove)}")
            lines.append(f"    unchanged: {_format_entries(diff.unchanged)}")
            lines.append(f"    route_has_changes: {str(route_diff.has_changes).lower()}")
            lines.append(
                f"    route_current: {_format_route_binding_state(route_diff.current_binding)}"
            )
            lines.append(
                f"    route_desired: {_format_route_binding_spec(route_diff.desired_binding)}"
            )

    return "\n".join(lines)


def _render_dry_run_json(result: DryRunExecutionResult) -> str:
    return _render_operation_json(result=result)


def _render_sync_json(result: SyncExecutionResult) -> str:
    return _render_operation_json(result=result)


def _render_operation_json(
    *,
    result: DryRunExecutionResult | SyncExecutionResult,
) -> str:
    payload = {
        "artifact_path": str(result.artifact_path),
        "artifact": result.artifact.model_dump(mode="json"),
        "plans": [_serialize_service_sync_plan(plan) for plan in result.plans],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _serialize_service_sync_plan(plan: ServiceSyncPlan) -> dict[str, object]:
    return {
        "service_key": plan.service_key,
        "router_id": plan.router_id,
        "object_group_name": plan.object_group_name,
        "object_group_diff": plan.object_group_diff.model_dump(mode="json"),
        "desired_route_binding": plan.desired_route_binding.model_dump(mode="json"),
        "route_binding_diff": plan.route_binding_diff.model_dump(mode="json"),
        "has_changes": plan.has_changes,
    }


def _format_entries(entries: tuple[str, ...]) -> str:
    if not entries:
        return "-"
    return ", ".join(entries)


def _format_route_binding_state(binding) -> str:
    if not binding.exists:
        return "absent"
    return _format_route_binding_spec(binding)


def _format_route_binding_spec(binding) -> str:
    parts = [
        f"type={binding.route_target_type}",
        f"value={binding.route_target_value}",
    ]
    if binding.route_interface is not None:
        parts.append(f"interface={binding.route_interface}")
    parts.append(f"auto={'yes' if binding.auto else 'no'}")
    parts.append(f"exclusive={'yes' if binding.exclusive else 'no'}")
    return " ".join(parts)


def _dry_run_exit_code(result: DryRunExecutionResult) -> int:
    if result.artifact.status in {RunStatus.PARTIAL, RunStatus.FAILED}:
        return 20
    if any(plan.has_changes for plan in result.plans):
        return 30
    return 0


def _sync_exit_code(result: SyncExecutionResult) -> int:
    if result.artifact.status in {RunStatus.PARTIAL, RunStatus.FAILED}:
        return 20
    if any(plan.has_changes for plan in result.plans):
        return 10
    return 0


def _runtime_error_handler(exc: RuntimeError, *, code: int = 1) -> NoReturn:
    typer.echo(str(exc), err=True)
    raise typer.Exit(code=code) from exc


def main() -> None:
    app()
