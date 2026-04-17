from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

import fqdn_updater.cli.panel as panel_module
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


def test_runs_menu_shows_history_and_back_does_not_trigger_status_or_dry_run(
    tmp_path,
    monkeypatch,
) -> None:
    controller, console = _panel_controller(tmp_path)
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
    status_service = _RecordingStatusService(result=_status_result())
    dry_run_orchestrator = _RecordingDryRunOrchestrator(result=_dry_run_result())
    controller._load_config = lambda: config  # type: ignore[method-assign]
    controller._run_history_service = run_history_service  # type: ignore[method-assign]
    controller._status_service = status_service  # type: ignore[method-assign]
    controller._dry_run_orchestrator = dry_run_orchestrator  # type: ignore[method-assign]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    prompt_calls: list[tuple[str, list[str] | None]] = []
    monkeypatch.setattr(
        panel_module.Prompt,
        "ask",
        _make_prompt_stub(prompt_calls, ("0",)),
    )

    controller._runs_menu(config=config)

    assert run_history_service.calls == [("data/artifacts", controller._config_path, 8)]
    assert status_service.calls == []
    assert dry_run_orchestrator.calls == []
    assert prompt_calls[0][1] == ["0", "1", "2"]
    output = console.export_text()
    assert "Recent local artifacts" in output
    assert "run-001" in output
    assert "changed=1 failed=1" in output
    assert "broken.json" in output
    assert "fqdn-updater sync --config" in output


def test_runs_menu_status_choice_calls_diagnostics_service_and_renders_router_details(
    tmp_path,
    monkeypatch,
) -> None:
    controller, console = _panel_controller(tmp_path)
    config = _config()
    status_result = _status_result()
    run_history_service = _RecordingRunHistoryService(
        result=RunHistoryResult(artifacts_dir=tmp_path / "data" / "artifacts", runs=(), warnings=())
    )
    status_service = _RecordingStatusService(result=status_result)
    dry_run_orchestrator = _RecordingDryRunOrchestrator(result=_dry_run_result())
    controller._load_config = lambda: config  # type: ignore[method-assign]
    controller._run_history_service = run_history_service  # type: ignore[method-assign]
    controller._status_service = status_service  # type: ignore[method-assign]
    controller._dry_run_orchestrator = dry_run_orchestrator  # type: ignore[method-assign]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    prompt_answers = iter(("1", "", "0"))
    prompt_calls: list[tuple[str, list[str] | None]] = []
    monkeypatch.setattr(
        panel_module.Prompt,
        "ask",
        _make_prompt_stub(prompt_calls, prompt_answers),
    )

    controller._runs_menu(config=config)

    assert len(status_service.calls) == 1
    assert dry_run_orchestrator.calls == []
    output = console.export_text()
    assert "Status diagnostics: overall=failed checked=3" in output
    assert "router-healthy" in output
    assert "healthy" in output
    assert "router-degraded" in output
    assert "degraded" in output
    assert "router-failed" in output
    assert "failed" in output
    assert "dns proxy disabled" in output
    assert "missing secret" in output


def test_runs_menu_dry_run_choice_calls_orchestrator_and_renders_summary(
    tmp_path,
    monkeypatch,
) -> None:
    controller, console = _panel_controller(tmp_path)
    config = _config()
    run_history_service = _RecordingRunHistoryService(
        result=RunHistoryResult(artifacts_dir=tmp_path / "data" / "artifacts", runs=(), warnings=())
    )
    status_service = _RecordingStatusService(result=_status_result())
    dry_run_result = _dry_run_result()
    dry_run_orchestrator = _RecordingDryRunOrchestrator(result=dry_run_result)
    controller._load_config = lambda: config  # type: ignore[method-assign]
    controller._run_history_service = run_history_service  # type: ignore[method-assign]
    controller._status_service = status_service  # type: ignore[method-assign]
    controller._dry_run_orchestrator = dry_run_orchestrator  # type: ignore[method-assign]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    prompt_answers = iter(("2", "", "0"))
    prompt_calls: list[tuple[str, list[str] | None]] = []
    monkeypatch.setattr(
        panel_module.Prompt,
        "ask",
        _make_prompt_stub(prompt_calls, prompt_answers),
    )

    controller._runs_menu(config=config)

    assert len(dry_run_orchestrator.calls) == 1
    assert dry_run_orchestrator.calls[0][1] is RunTrigger.MANUAL
    assert status_service.calls == []
    output = console.export_text()
    assert "Dry-run: run_id=run-123 status=partial artifact=data/artifacts/run-123.json" in output
    assert "changed=1 failed=1" in output
    assert "router-1" in output
    assert "partial" in output


def _panel_controller(tmp_path: Path) -> tuple[PanelController, Console]:
    console = Console(force_terminal=True, record=True, width=120)
    return PanelController(config_path=tmp_path / "config.json", console=console), console


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "routers": [],
            "services": [],
            "mappings": [],
            "runtime": {
                "artifacts_dir": "data/artifacts",
                "logs_dir": "data/logs",
                "secrets_env_file": ".env.secrets",
            },
        }
    )


def _status_result() -> StatusDiagnosticsResult:
    return StatusDiagnosticsResult(
        config_ready=True,
        overall_status=OverallDiagnosticStatus.FAILED,
        checked_router_count=3,
        router_results=(
            RouterStatusDiagnostic(
                router_id="router-healthy",
                status=RouterDiagnosticStatus.HEALTHY,
                dns_proxy_enabled=True,
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
                error_message="missing secret",
            ),
        ),
    )


def _dry_run_result() -> DryRunExecutionResult:
    return DryRunExecutionResult(
        artifact=_artifact(
            run_id="run-123",
            status=RunStatus.PARTIAL,
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
                    error_message="planning failed",
                ),
            ),
        ),
        artifact_path=Path("data/artifacts/run-123.json"),
        plans=(),
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
        log_path=Path("data/logs") / f"{run_id}.log",
        router_results=[
            RouterRunResult(
                router_id="router-1",
                status=(
                    RouterResultStatus.PARTIAL
                    if status is RunStatus.PARTIAL
                    else RouterResultStatus.NO_CHANGES
                ),
                service_results=list(service_results),
            )
        ],
    )


def _make_prompt_stub(
    prompt_calls: list[tuple[str, list[str] | None]],
    answers: Iterable[str],
):
    answer_iter = iter(answers)

    def _ask(*args, **kwargs) -> str:
        prompt_calls.append(
            (str(args[0]), list(kwargs.get("choices")) if kwargs.get("choices") else None)
        )
        return next(answer_iter)

    return _ask
