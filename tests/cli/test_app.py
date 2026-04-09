from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

import fqdn_updater.cli.app as cli_app_module
from fqdn_updater.application.dry_run_orchestration import DryRunExecutionResult, DryRunOrchestrator
from fqdn_updater.application.keenetic_client import KeeneticClient, KeeneticClientFactory
from fqdn_updater.application.service_sync_planning import ServiceSyncPlan, ServiceSyncPlanner
from fqdn_updater.application.sync_orchestration import SyncExecutionResult
from fqdn_updater.cli.app import app
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig
from fqdn_updater.domain.keenetic import (
    DnsProxyStatus,
    ObjectGroupState,
    RouteBindingDiff,
    RouteBindingSpec,
    RouteBindingState,
)
from fqdn_updater.domain.object_group_diff import ObjectGroupDiff
from fqdn_updater.domain.run_artifact import (
    RouterResultStatus,
    RouterRunResult,
    RunArtifact,
    RunMode,
    RunStatus,
    RunStep,
    RunTrigger,
    ServiceResultStatus,
    ServiceRunResult,
)
from fqdn_updater.domain.source_loading import NormalizedServiceSource, SourceLoadReport
from fqdn_updater.domain.status_diagnostics import (
    OverallDiagnosticStatus,
    RouterDiagnosticStatus,
    RouterStatusDiagnostic,
    StatusDiagnosticsResult,
)

runner = CliRunner()


def test_root_help_shows_expected_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "init" in result.stdout
    assert "config" in result.stdout
    assert "dry-run" in result.stdout
    assert "sync" in result.stdout
    assert "status" in result.stdout


def test_init_creates_scaffold_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"

    result = runner.invoke(app, ["init", "--config", str(config_path)])

    assert result.exit_code == 0
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["routers"] == []
    assert [service["key"] for service in payload["services"]] == [
        "news",
        "hdrezka",
        "meta",
        "tiktok",
        "twitter",
        "youtube",
        "discord",
        "cloudflare",
        "telegram",
        "google_meet",
        "google_ai",
    ]
    assert payload["services"][-1]["source_urls"] == [
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Services/google_ai.lst"
    ]
    assert payload["mappings"] == []


def test_init_refuses_to_overwrite_existing_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["init", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "already exists" in result.stderr


def test_validate_accepts_scaffold_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"

    init_result = runner.invoke(app, ["init", "--config", str(config_path)])
    validate_result = runner.invoke(app, ["config", "validate", "--config", str(config_path)])

    assert init_result.exit_code == 0
    assert validate_result.exit_code == 0
    assert "Config is valid" in validate_result.stdout


def test_validate_rejects_invalid_json(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{not-json", encoding="utf-8")

    result = runner.invoke(app, ["config", "validate", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Invalid JSON" in result.stderr


def test_validate_rejects_invalid_structure(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "wrong",
                "routers": [],
                "services": [],
                "mappings": [],
                "runtime": {},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["config", "validate", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Config validation failed" in result.stderr


def test_validate_reports_semantic_errors_with_clean_location(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "routers": [
                    {
                        "id": "router-1",
                        "name": "Router 1",
                        "rci_url": "https://router-1.example/rci/",
                        "username": "api-user",
                        "password_env": "ROUTER_ONE_SECRET",
                        "enabled": True,
                    },
                    {
                        "id": "router-1",
                        "name": "Router 2",
                        "rci_url": "https://router-2.example/rci/",
                        "username": "api-user",
                        "password_file": "/run/secrets/router-2",
                        "enabled": True,
                    },
                ],
                "services": [],
                "mappings": [],
                "runtime": {},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["config", "validate", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Config validation failed" in result.stderr
    assert "- config: duplicate router id 'router-1'" in result.stderr
    assert "Value error" not in result.stderr


def test_dry_run_returns_zero_for_no_changes(monkeypatch) -> None:
    config = _config()
    result_payload = _dry_run_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-100.json"),
        plans=(
            _plan(
                to_add=(),
                to_remove=(),
                unchanged=("keep.example",),
                has_changes=False,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.NO_CHANGES,
                unchanged_count=1,
                route_changed=False,
            ),
        ),
        router_status=RouterResultStatus.NO_CHANGES,
    )
    _install_dry_run_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["dry-run", "--config", "config.json"])

    assert result.exit_code == 0
    assert "Dry run completed:" in result.stdout
    assert "planned_changes=0" in result.stdout
    assert "status=no_changes" in result.stdout


def test_dry_run_returns_thirty_for_changes_and_json_output(monkeypatch) -> None:
    config = _config()
    result_payload = _dry_run_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-101.json"),
        plans=(
            _plan(
                to_add=("new.example",),
                to_remove=("old.example",),
                unchanged=("keep.example",),
                has_changes=True,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.UPDATED,
                added_count=1,
                removed_count=1,
                unchanged_count=1,
                route_changed=True,
            ),
        ),
        router_status=RouterResultStatus.UPDATED,
    )
    _install_dry_run_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["dry-run", "--config", "config.json", "--output", "json"])

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["artifact_path"] == "data/artifacts/run-101.json"
    assert payload["artifact"]["status"] == "success"
    assert payload["plans"] == [
        {
            "desired_route_binding": {
                "auto": True,
                "exclusive": False,
                "object_group_name": "svc-telegram",
                "route_interface": None,
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
            },
            "has_changes": True,
            "object_group_diff": {
                "has_changes": True,
                "needs_create": False,
                "object_group_name": "svc-telegram",
                "to_add": ["new.example"],
                "to_remove": ["old.example"],
                "unchanged": ["keep.example"],
            },
            "object_group_name": "svc-telegram",
            "router_id": "router-1",
            "route_binding_diff": {
                "current_binding": {
                    "auto": True,
                    "exclusive": False,
                    "exists": True,
                    "object_group_name": "svc-telegram",
                    "route_interface": None,
                    "route_target_type": "interface",
                    "route_target_value": "Other0",
                },
                "desired_binding": {
                    "auto": True,
                    "exclusive": False,
                    "object_group_name": "svc-telegram",
                    "route_interface": None,
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                },
                "has_changes": True,
                "object_group_name": "svc-telegram",
            },
            "service_key": "telegram",
        }
    ]


def test_dry_run_returns_twenty_for_partial_result(monkeypatch) -> None:
    config = _config()
    result_payload = _dry_run_result(
        status=RunStatus.PARTIAL,
        artifact_path=Path("data/artifacts/run-102.json"),
        plans=(
            _plan(
                to_add=("new.example",),
                to_remove=(),
                unchanged=(),
                has_changes=True,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.UPDATED,
                added_count=1,
            ),
            ServiceRunResult(
                service_key="youtube",
                object_group_name="svc-youtube",
                status=ServiceResultStatus.FAILED,
                error_message="timeout",
            ),
        ),
        router_status=RouterResultStatus.PARTIAL,
    )
    _install_dry_run_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["dry-run", "--config", "config.json"])

    assert result.exit_code == 20
    assert "status=partial" in result.stdout
    assert "failed_services=1" in result.stdout
    assert "error: timeout" in result.stdout


def test_dry_run_returns_thirty_for_route_only_changes(monkeypatch) -> None:
    config = _config()
    result_payload = _dry_run_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-105.json"),
        plans=(_route_only_plan(),),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.UPDATED,
                unchanged_count=1,
                route_changed=True,
            ),
        ),
        router_status=RouterResultStatus.UPDATED,
    )
    _install_dry_run_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["dry-run", "--config", "config.json"])

    assert result.exit_code == 30
    assert "planned_changes=1" in result.stdout
    assert "route_changed=yes" in result.stdout


def test_dry_run_returns_forty_for_invalid_config(monkeypatch) -> None:
    class FailingValidationService:
        def validate(self, path: Path) -> AppConfig:
            raise RuntimeError(f"Invalid JSON in config file {path}: broken")

    monkeypatch.setattr(cli_app_module, "_validation_service", lambda: FailingValidationService())

    result = runner.invoke(app, ["dry-run", "--config", "missing.json"])

    assert result.exit_code == 40
    assert "Invalid JSON in config file missing.json: broken" in result.stderr


def test_sync_returns_zero_for_no_changes(monkeypatch) -> None:
    config = _config()
    result_payload = _sync_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-200.json"),
        plans=(
            _plan(
                to_add=(),
                to_remove=(),
                unchanged=("keep.example",),
                has_changes=False,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.NO_CHANGES,
                unchanged_count=1,
                route_changed=False,
            ),
        ),
        router_status=RouterResultStatus.NO_CHANGES,
    )
    _install_sync_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["sync", "--config", "config.json"])

    assert result.exit_code == 0
    assert "Sync completed:" in result.stdout
    assert "planned_changes=0" in result.stdout
    assert "skipped_services=0" in result.stdout


def test_sync_returns_ten_for_applied_changes_and_json_output(monkeypatch) -> None:
    config = _config()
    result_payload = _sync_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-201.json"),
        plans=(
            _plan(
                to_add=("new.example",),
                to_remove=("old.example",),
                unchanged=("keep.example",),
                has_changes=True,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.UPDATED,
                added_count=1,
                removed_count=1,
                unchanged_count=1,
                route_changed=True,
            ),
        ),
        router_status=RouterResultStatus.UPDATED,
    )
    _install_sync_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["sync", "--config", "config.json", "--output", "json"])

    assert result.exit_code == 10
    payload = json.loads(result.stdout)
    assert payload["artifact_path"] == "data/artifacts/run-201.json"
    assert payload["artifact"]["mode"] == "apply"
    assert payload["artifact"]["status"] == "success"
    assert payload["plans"][0]["object_group_diff"]["has_changes"] is True
    assert payload["plans"][0]["route_binding_diff"]["has_changes"] is True


def test_sync_returns_twenty_for_partial_result(monkeypatch) -> None:
    config = _config()
    result_payload = _sync_result(
        status=RunStatus.PARTIAL,
        artifact_path=Path("data/artifacts/run-202.json"),
        plans=(
            _plan(
                to_add=("new.example",),
                to_remove=(),
                unchanged=(),
                has_changes=True,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.FAILED,
                error_message="Write stage failed for service 'telegram': timeout",
            ),
            ServiceRunResult(
                service_key="youtube",
                object_group_name="svc-youtube",
                status=ServiceResultStatus.SKIPPED,
                error_message="Skipped after router write failure: timeout",
            ),
        ),
        router_status=RouterResultStatus.PARTIAL,
    )
    _install_sync_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["sync", "--config", "config.json"])

    assert result.exit_code == 20
    assert "status=partial" in result.stdout
    assert "skipped_services=1" in result.stdout
    assert "error: Skipped after router write failure: timeout" in result.stdout


def test_sync_returns_ten_for_route_only_changes(monkeypatch) -> None:
    config = _config()
    result_payload = _sync_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-203.json"),
        plans=(_route_only_plan(),),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.UPDATED,
                unchanged_count=1,
                route_changed=True,
            ),
        ),
        router_status=RouterResultStatus.UPDATED,
    )
    _install_sync_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["sync", "--config", "config.json"])

    assert result.exit_code == 10
    assert "planned_changes=1" in result.stdout
    assert "route_changed=yes" in result.stdout


def test_sync_returns_forty_for_invalid_config(monkeypatch) -> None:
    class FailingValidationService:
        def validate(self, path: Path) -> AppConfig:
            raise RuntimeError(f"Config validation failed for {path}")

    monkeypatch.setattr(cli_app_module, "_validation_service", lambda: FailingValidationService())

    result = runner.invoke(app, ["sync", "--config", "missing.json"])

    assert result.exit_code == 40
    assert "Config validation failed for missing.json" in result.stderr


def test_status_returns_zero_for_healthy_result(monkeypatch) -> None:
    config = _config()
    result_payload = _status_result(
        overall_status=OverallDiagnosticStatus.HEALTHY,
        router_results=(
            RouterStatusDiagnostic(
                router_id="router-1",
                status=RouterDiagnosticStatus.HEALTHY,
                dns_proxy_enabled=True,
            ),
        ),
    )
    _install_status_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["status", "--config", "config.json"])

    assert result.exit_code == 0
    assert "Status completed:" in result.stdout
    assert "overall_status=healthy" in result.stdout
    assert "dns_proxy=enabled" in result.stdout


def test_status_returns_twenty_for_degraded_result_and_json_output(monkeypatch) -> None:
    config = _config()
    result_payload = _status_result(
        overall_status=OverallDiagnosticStatus.DEGRADED,
        router_results=(
            RouterStatusDiagnostic(
                router_id="router-1",
                status=RouterDiagnosticStatus.DEGRADED,
                dns_proxy_enabled=False,
            ),
        ),
    )
    _install_status_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["status", "--config", "config.json", "--output", "json"])

    assert result.exit_code == 20
    payload = json.loads(result.stdout)
    assert payload["config_ready"] is True
    assert payload["overall_status"] == "degraded"
    assert payload["checked_router_count"] == 1
    assert payload["router_results"][0]["dns_proxy_enabled"] is False


def test_status_returns_twenty_for_failed_result(monkeypatch) -> None:
    config = _config()
    result_payload = _status_result(
        overall_status=OverallDiagnosticStatus.FAILED,
        router_results=(
            RouterStatusDiagnostic(
                router_id="router-1",
                status=RouterDiagnosticStatus.FAILED,
                dns_proxy_enabled=None,
                error_message="authentication failed",
                failure_step=RunStep.CLIENT_CREATE,
            ),
        ),
    )
    _install_status_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["status", "--config", "config.json"])

    assert result.exit_code == 20
    assert "status=failed" in result.stdout
    assert "failure_step: client_create" in result.stdout
    assert "error: authentication failed" in result.stdout


def test_status_returns_forty_for_invalid_config(monkeypatch) -> None:
    class FailingValidationService:
        def validate(self, path: Path) -> AppConfig:
            raise RuntimeError(f"Config validation failed for {path}")

    monkeypatch.setattr(cli_app_module, "_validation_service", lambda: FailingValidationService())

    result = runner.invoke(app, ["status", "--config", "missing.json"])

    assert result.exit_code == 40
    assert "Config validation failed for missing.json" in result.stderr


def test_dry_run_never_calls_write_methods(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config().model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv("ROUTER_ONE_SECRET", "secret-1")

    artifact_writer = RecordingArtifactWriter()
    client_factory = RecordingClientFactory(
        states={
            ("router-1", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=("keep.example",),
                exists=True,
            )
        },
        route_bindings={
            ("router-1", "svc-telegram"): RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Wireguard0",
                auto=True,
                exclusive=False,
            )
        },
    )
    orchestrator = DryRunOrchestrator(
        source_loader=StubSourceLoader(
            SourceLoadReport(
                loaded=(
                    NormalizedServiceSource(
                        service_key="telegram",
                        entries=("keep.example", "new.example"),
                    ),
                )
            )
        ),
        secret_resolver=cli_app_module.EnvironmentFileSecretResolver(),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=artifact_writer,
        now_provider=lambda: datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
        run_id_factory=lambda: "run-103",
    )
    monkeypatch.setattr(cli_app_module, "_dry_run_orchestrator", lambda: orchestrator)

    result = runner.invoke(app, ["dry-run", "--config", str(config_path)])

    assert result.exit_code == 30
    assert artifact_writer.last_artifact is not None
    assert client_factory.clients["router-1"].read_calls == [
        "svc-telegram",
        "route:svc-telegram",
    ]
    assert client_factory.clients["router-1"].write_calls == []


class StubValidationService:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def validate(self, path: Path) -> AppConfig:
        return self._config


class StubOrchestrator:
    def __init__(self, result: DryRunExecutionResult) -> None:
        self._result = result

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> DryRunExecutionResult:
        assert trigger is RunTrigger.MANUAL
        return self._result


class StubSyncOrchestrator:
    def __init__(self, result: SyncExecutionResult) -> None:
        self._result = result

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> SyncExecutionResult:
        assert trigger is RunTrigger.MANUAL
        return self._result


class StubStatusService:
    def __init__(self, result: StatusDiagnosticsResult) -> None:
        self._result = result

    def check(self, *, config: AppConfig) -> StatusDiagnosticsResult:
        return self._result


class StubSourceLoader:
    def __init__(self, report: SourceLoadReport) -> None:
        self._report = report

    def load_enabled_services(self, services: object) -> SourceLoadReport:
        return self._report


class RecordingArtifactWriter:
    def __init__(self) -> None:
        self.last_artifact: RunArtifact | None = None

    def write(self, config: AppConfig, artifact: RunArtifact) -> Path:
        self.last_artifact = artifact
        return Path(config.runtime.artifacts_dir) / f"{artifact.run_id}.json"


class RecordingClient(KeeneticClient):
    def __init__(
        self,
        states: dict[str, ObjectGroupState],
        route_bindings: dict[str, RouteBindingState],
    ) -> None:
        self._states = states
        self._route_bindings = route_bindings
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []

    def get_object_group(self, name: str) -> ObjectGroupState:
        self.read_calls.append(name)
        return self._states[name]

    def get_route_binding(self, object_group_name: str) -> RouteBindingState:
        self.read_calls.append(f"route:{object_group_name}")
        return self._route_bindings[object_group_name]

    def ensure_object_group(self, name: str) -> None:
        self.write_calls.append(f"ensure_object_group:{name}")

    def add_entries(self, name: str, items: object) -> None:
        self.write_calls.append(f"add_entries:{name}")

    def remove_entries(self, name: str, items: object) -> None:
        self.write_calls.append(f"remove_entries:{name}")

    def ensure_route(self, binding: RouteBindingSpec) -> None:
        self.write_calls.append(f"ensure_route:{binding.object_group_name}")

    def save_config(self) -> None:
        self.write_calls.append("save_config")

    def get_dns_proxy_status(self) -> DnsProxyStatus:
        return DnsProxyStatus(enabled=True)


class RecordingClientFactory(KeeneticClientFactory):
    def __init__(
        self,
        states: dict[tuple[str, str], ObjectGroupState],
        route_bindings: dict[tuple[str, str], RouteBindingState],
    ) -> None:
        self._states = states
        self._route_bindings = route_bindings
        self.clients: dict[str, RecordingClient] = {}

    def create(self, router: RouterConfig, password: str) -> KeeneticClient:
        router_states = {
            group_name: state
            for (router_id, group_name), state in self._states.items()
            if router_id == router.id
        }
        router_route_bindings = {
            group_name: state
            for (router_id, group_name), state in self._route_bindings.items()
            if router_id == router.id
        }
        client = RecordingClient(states=router_states, route_bindings=router_route_bindings)
        self.clients[router.id] = client
        return client


def _install_dry_run_stubs(
    monkeypatch,
    *,
    config: AppConfig,
    result: DryRunExecutionResult,
) -> None:
    monkeypatch.setattr(
        cli_app_module,
        "_validation_service",
        lambda: StubValidationService(config=config),
    )
    monkeypatch.setattr(
        cli_app_module,
        "_dry_run_orchestrator",
        lambda: StubOrchestrator(result=result),
    )


def _install_sync_stubs(
    monkeypatch,
    *,
    config: AppConfig,
    result: SyncExecutionResult,
) -> None:
    monkeypatch.setattr(
        cli_app_module,
        "_validation_service",
        lambda: StubValidationService(config=config),
    )
    monkeypatch.setattr(
        cli_app_module,
        "_sync_orchestrator",
        lambda: StubSyncOrchestrator(result=result),
    )


def _install_status_stubs(
    monkeypatch,
    *,
    config: AppConfig,
    result: StatusDiagnosticsResult,
) -> None:
    monkeypatch.setattr(
        cli_app_module,
        "_validation_service",
        lambda: StubValidationService(config=config),
    )
    monkeypatch.setattr(
        cli_app_module,
        "_status_service",
        lambda: StubStatusService(result=result),
    )


def _dry_run_result(
    *,
    status: RunStatus,
    artifact_path: Path,
    plans: tuple[ServiceSyncPlan, ...],
    service_results: tuple[ServiceRunResult, ...],
    router_status: RouterResultStatus,
) -> DryRunExecutionResult:
    return DryRunExecutionResult(
        artifact=RunArtifact(
            run_id=artifact_path.stem,
            trigger=RunTrigger.MANUAL,
            mode=RunMode.DRY_RUN,
            status=status,
            started_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
            finished_at=datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
            log_path=Path("data/logs") / f"{artifact_path.stem}.log",
            router_results=[
                RouterRunResult(
                    router_id="router-1",
                    status=router_status,
                    service_results=list(service_results),
                )
            ],
        ),
        artifact_path=artifact_path,
        plans=plans,
    )


def _sync_result(
    *,
    status: RunStatus,
    artifact_path: Path,
    plans: tuple[ServiceSyncPlan, ...],
    service_results: tuple[ServiceRunResult, ...],
    router_status: RouterResultStatus,
) -> SyncExecutionResult:
    return SyncExecutionResult(
        artifact=RunArtifact(
            run_id=artifact_path.stem,
            trigger=RunTrigger.MANUAL,
            mode=RunMode.APPLY,
            status=status,
            started_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
            finished_at=datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
            log_path=Path("data/logs") / f"{artifact_path.stem}.log",
            router_results=[
                RouterRunResult(
                    router_id="router-1",
                    status=router_status,
                    service_results=list(service_results),
                )
            ],
        ),
        artifact_path=artifact_path,
        plans=plans,
    )


def _status_result(
    *,
    overall_status: OverallDiagnosticStatus,
    router_results: tuple[RouterStatusDiagnostic, ...],
) -> StatusDiagnosticsResult:
    return StatusDiagnosticsResult(
        config_ready=True,
        overall_status=overall_status,
        checked_router_count=len(router_results),
        router_results=router_results,
    )


def _plan(
    *,
    to_add: tuple[str, ...],
    to_remove: tuple[str, ...],
    unchanged: tuple[str, ...],
    has_changes: bool,
) -> ServiceSyncPlan:
    desired_route_binding = RouteBindingSpec(
        object_group_name="svc-telegram",
        route_target_type="interface",
        route_target_value="Wireguard0",
        route_interface=None,
        auto=True,
        exclusive=False,
    )
    current_route_value = "Other0" if has_changes else "Wireguard0"
    return ServiceSyncPlan(
        service_key="telegram",
        router_id="router-1",
        object_group_name="svc-telegram",
        object_group_diff=ObjectGroupDiff(
            object_group_name="svc-telegram",
            needs_create=False,
            to_add=to_add,
            to_remove=to_remove,
            unchanged=unchanged,
            has_changes=has_changes,
        ),
        desired_route_binding=desired_route_binding,
        route_binding_diff=RouteBindingDiff(
            object_group_name="svc-telegram",
            current_binding=RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value=current_route_value,
                auto=True,
                exclusive=False,
            ),
            desired_binding=desired_route_binding,
            has_changes=has_changes,
        ),
    )


def _route_only_plan() -> ServiceSyncPlan:
    desired_route_binding = RouteBindingSpec(
        object_group_name="svc-telegram",
        route_target_type="interface",
        route_target_value="Wireguard0",
        route_interface=None,
        auto=True,
        exclusive=False,
    )
    return ServiceSyncPlan(
        service_key="telegram",
        router_id="router-1",
        object_group_name="svc-telegram",
        object_group_diff=ObjectGroupDiff(
            object_group_name="svc-telegram",
            needs_create=False,
            to_add=(),
            to_remove=(),
            unchanged=("keep.example",),
            has_changes=False,
        ),
        desired_route_binding=desired_route_binding,
        route_binding_diff=RouteBindingDiff(
            object_group_name="svc-telegram",
            current_binding=RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Other0",
                auto=False,
                exclusive=False,
            ),
            desired_binding=desired_route_binding,
            has_changes=True,
        ),
    )


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "version": 1,
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_SECRET",
                    "enabled": True,
                }
            ],
            "services": [
                {
                    "key": "telegram",
                    "source_urls": ["https://example.com/telegram.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                }
            ],
            "mappings": [
                {
                    "router_id": "router-1",
                    "service_key": "telegram",
                    "object_group_name": "svc-telegram",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                }
            ],
            "runtime": {"artifacts_dir": "data/artifacts", "logs_dir": "data/logs"},
        }
    )
