from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fqdn_updater.application.dry_run_orchestration import DryRunExecutionResult
from fqdn_updater.application.run_history import RecentRun, RunHistoryResult, RunHistoryWarning
from fqdn_updater.cli.panel import PanelController
from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.run_artifact import (
    RouterResultStatus,
    RouterRunResult,
    RunArtifact,
    RunMode,
    RunStatus,
    RunTrigger,
    ServiceResultStatus,
    ServiceRunResult,
)
from fqdn_updater.domain.status_diagnostics import (
    OverallDiagnosticStatus,
    RouterDiagnosticStatus,
    RouterStatusDiagnostic,
    StatusDiagnosticsResult,
)

from .panel_test_support import ScriptedPromptAdapter, make_panel_controller


class _RecordingRunHistoryService:
    def __init__(self, result: RunHistoryResult) -> None:
        self.result = result
        self.calls: list[tuple[str, Path, int]] = []

    def list_recent(
        self,
        *,
        config: AppConfig,
        config_path: Path,
        limit: int = 10,
    ) -> RunHistoryResult:
        self.calls.append((config.runtime.artifacts_dir, config_path, limit))
        return self.result


class _RecordingStatusService:
    def __init__(self, result: StatusDiagnosticsResult) -> None:
        self.result = result
        self.calls: list[AppConfig] = []

    def check(self, *, config: AppConfig) -> StatusDiagnosticsResult:
        self.calls.append(config)
        return self.result


class _RecordingDryRunOrchestrator:
    def __init__(self, result: DryRunExecutionResult) -> None:
        self.result = result
        self.calls: list[tuple[AppConfig, RunTrigger]] = []

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> DryRunExecutionResult:
        self.calls.append((config, trigger))
        return self.result


def test_runs_menu_shows_history_and_back_does_not_trigger_status_or_dry_run(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["back"])
    controller, console = _panel_controller(tmp_path, prompts=prompts)
    config = _config()
    history = RunHistoryResult(
        artifacts_dir=tmp_path / "data" / "artifacts",
        runs=(
            RecentRun(
                path=tmp_path / "data" / "artifacts" / "run-001.json",
                artifact=_artifact(
                    run_id="run-001",
                    status=RunStatus.SUCCESS,
                    finished_at=datetime(2026, 4, 8, 13, 1, tzinfo=UTC),
                    service_results=(
                        ServiceRunResult(
                            service_key="telegram",
                            object_group_name="svc-telegram",
                            status=ServiceResultStatus.UPDATED,
                            added_count=1,
                            route_changed=True,
                        ),
                        ServiceRunResult(
                            service_key="youtube",
                            object_group_name="svc-youtube",
                            status=ServiceResultStatus.FAILED,
                            error_message="router rejected update",
                        ),
                    ),
                ),
            ),
        ),
        warnings=(
            RunHistoryWarning(
                path=tmp_path / "data" / "artifacts" / "broken.json",
                message="artifact JSON is invalid: unexpected token",
            ),
        ),
    )
    run_history_service = _RecordingRunHistoryService(result=history)
    controller._load_config = lambda: config  # type: ignore[method-assign]
    controller._run_history_service = run_history_service  # type: ignore[method-assign]
    controller._status_service = _RecordingStatusService(result=_status_result())  # type: ignore[method-assign]
    controller._dry_run_orchestrator = _RecordingDryRunOrchestrator(result=_dry_run_result())  # type: ignore[method-assign]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    controller._runs_menu()

    assert run_history_service.calls == [("data/artifacts", controller._config_path, 8)]
    assert prompts.select_calls[0]["choices"] == ["status", "dry-run", "back"]
    output = console.export_text()
    assert "Прогоны и диагностика" in output
    assert "Контекст прогонов" in output
    assert "Последние локальные артефакты" in output
    assert "run-001" in output
    assert "changed=1 failed=1" in output
    assert "data/artifacts" in output
    assert "data/logs" in output
    assert "Пропущенные артефакты" in output
    assert "data/artifacts/broken.json" in output
    assert "artifact JSON is invalid: unexpected token" in output
    assert "Ручной запуск" in output
    assert "fqdn-updater sync --config" in output


def test_runs_menu_status_choice_calls_diagnostics_service_and_renders_router_details(
    tmp_path,
) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["status", "back"])
    controller, console = _panel_controller(tmp_path, prompts=prompts)
    config = _config()
    status_result = _status_result()
    run_history_service = _RecordingRunHistoryService(
        result=RunHistoryResult(artifacts_dir=tmp_path / "data" / "artifacts", runs=(), warnings=())
    )
    status_service = _RecordingStatusService(result=status_result)
    controller._load_config = lambda: config  # type: ignore[method-assign]
    controller._run_history_service = run_history_service  # type: ignore[method-assign]
    controller._status_service = status_service  # type: ignore[method-assign]
    controller._dry_run_orchestrator = _RecordingDryRunOrchestrator(result=_dry_run_result())  # type: ignore[method-assign]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    controller._runs_menu()

    assert len(status_service.calls) == 1
    assert prompts.pause_messages == ["Нажмите любую клавишу для продолжения..."]
    output = console.export_text()
    assert "Status diagnostics: overall=failed checked=3" in output
    assert "router-healthy" in output
    assert "router-degraded" in output
    assert "router-failed" in output
    assert "dns proxy disabled" in output
    assert "missing secret" in output


def test_runs_menu_dry_run_choice_calls_orchestrator_and_renders_summary(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["dry-run", "back"])
    controller, console = _panel_controller(tmp_path, prompts=prompts)
    config = _config()
    run_history_service = _RecordingRunHistoryService(
        result=RunHistoryResult(artifacts_dir=tmp_path / "data" / "artifacts", runs=(), warnings=())
    )
    dry_run_result = _dry_run_result()
    dry_run_orchestrator = _RecordingDryRunOrchestrator(result=dry_run_result)
    controller._load_config = lambda: config  # type: ignore[method-assign]
    controller._run_history_service = run_history_service  # type: ignore[method-assign]
    controller._status_service = _RecordingStatusService(result=_status_result())  # type: ignore[method-assign]
    controller._dry_run_orchestrator = dry_run_orchestrator  # type: ignore[method-assign]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    controller._runs_menu()

    assert len(dry_run_orchestrator.calls) == 1
    assert dry_run_orchestrator.calls[0][1] is RunTrigger.MANUAL
    output = console.export_text()
    assert "Dry-run: run_id=run-123 status=partial artifact=data/artifacts/run-123.json" in output
    assert "changed=1 failed=1" in output
    assert "router-1" in output
    assert "partial" in output


def _panel_controller(
    tmp_path: Path,
    *,
    prompts: ScriptedPromptAdapter,
) -> tuple[PanelController, object]:
    return make_panel_controller(tmp_path, prompts=prompts)


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "version": 1,
            "routers": [],
            "services": [],
            "mappings": [],
            "runtime": {"artifacts_dir": "data/artifacts", "logs_dir": "data/logs"},
        }
    )


def _artifact(
    *,
    run_id: str,
    status: RunStatus,
    finished_at: datetime,
    service_results: tuple[ServiceRunResult, ...],
) -> RunArtifact:
    return RunArtifact(
        run_id=run_id,
        trigger=RunTrigger.MANUAL,
        mode=RunMode.DRY_RUN,
        status=status,
        started_at=datetime(2026, 4, 8, 13, 0, tzinfo=UTC),
        finished_at=finished_at,
        log_path=Path("data/logs/run.log"),
        router_results=(
            RouterRunResult(
                router_id="router-1",
                status=RouterResultStatus.PARTIAL,
                service_results=service_results,
            ),
        ),
    )


def _status_result() -> StatusDiagnosticsResult:
    return StatusDiagnosticsResult(
        overall_status=OverallDiagnosticStatus.FAILED,
        checked_router_count=3,
        router_results=(
            RouterStatusDiagnostic(
                router_id="router-healthy",
                status=RouterDiagnosticStatus.HEALTHY,
                dns_proxy_enabled=True,
                error_message=None,
            ),
            RouterStatusDiagnostic(
                router_id="router-degraded",
                status=RouterDiagnosticStatus.DEGRADED,
                dns_proxy_enabled=False,
                error_message="dns proxy disabled",
            ),
            RouterStatusDiagnostic(
                router_id="router-failed",
                status=RouterDiagnosticStatus.FAILED,
                dns_proxy_enabled=None,
                error_message="missing secret",
            ),
        ),
    )


def _dry_run_result() -> DryRunExecutionResult:
    artifact = RunArtifact(
        run_id="run-123",
        trigger=RunTrigger.MANUAL,
        mode=RunMode.DRY_RUN,
        status=RunStatus.PARTIAL,
        started_at=datetime(2026, 4, 8, 13, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 8, 13, 1, tzinfo=UTC),
        log_path=Path("data/logs/run-123.log"),
        router_results=(
            RouterRunResult(
                router_id="router-1",
                status=RouterResultStatus.PARTIAL,
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
                        error_message="router rejected update",
                    ),
                ),
            ),
        ),
    )
    return DryRunExecutionResult(
        artifact=artifact,
        artifact_path=Path("data/artifacts/run-123.json"),
        plans=(),
    )
