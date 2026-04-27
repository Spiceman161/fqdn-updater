from __future__ import annotations

from datetime import datetime, timezone
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

from .panel_test_support import ScriptedPromptAdapter, make_panel_controller


class _RecordingRunHistoryService:
    def __init__(
        self,
        *,
        artifacts_dir: Path,
        runs: tuple[RecentRun, ...],
        warnings: tuple[RunHistoryWarning, ...] = (),
    ) -> None:
        self.artifacts_dir = artifacts_dir
        self.runs = runs
        self.warnings = warnings
        self.calls: list[tuple[str, Path, int, int]] = []

    def list_recent(
        self,
        *,
        config: AppConfig,
        config_path: Path,
        limit: int = 10,
        offset: int = 0,
    ) -> RunHistoryResult:
        self.calls.append((config.runtime.artifacts_dir, config_path, limit, offset))
        return RunHistoryResult(
            artifacts_dir=self.artifacts_dir,
            runs=self.runs[offset : offset + limit],
            warnings=self.warnings,
            total_count=len(self.runs),
        )


class _RecordingDryRunOrchestrator:
    def __init__(self, result: DryRunExecutionResult) -> None:
        self.result = result
        self.calls: list[tuple[AppConfig, RunTrigger]] = []

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> DryRunExecutionResult:
        self.calls.append((config, trigger))
        return self.result


def test_runs_menu_shows_history_and_back_does_not_trigger_status_or_dry_run(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=[None])
    controller, console = _panel_controller(tmp_path, prompts=prompts)
    config = _config()
    artifacts_dir = tmp_path / "data" / "artifacts"
    run_history_service = _RecordingRunHistoryService(
        artifacts_dir=artifacts_dir,
        runs=(
            RecentRun(
                path=artifacts_dir / "run-001.json",
                artifact=_artifact(
                    run_id="run-001",
                    status=RunStatus.SUCCESS,
                    finished_at=datetime(2026, 4, 8, 13, 1, tzinfo=timezone.utc),
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
                path=artifacts_dir / "broken.json",
                message="artifact JSON is invalid: unexpected token",
            ),
        ),
    )
    controller._load_config = lambda: config  # type: ignore[method-assign]
    controller._run_history_service = run_history_service  # type: ignore[method-assign]
    controller._dry_run_orchestrator = _RecordingDryRunOrchestrator(result=_dry_run_result())  # type: ignore[method-assign]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    controller._runs_menu()

    assert run_history_service.calls == [("data/artifacts", controller._config_path, 10, 0)]
    assert prompts.select_calls == []
    assert prompts.history_select_calls[0]["message"] == "Журнал"
    assert prompts.history_select_calls[0]["choices"] == ["run:0"]
    assert prompts.history_select_calls[0]["choice_titles"] == [
        "dry_run   manual     success  08.04.2026 13:01:00  Main router  изменено=1 ошибок=1",
    ]
    assert prompts.history_select_calls[0]["page_label"] == "Страница 1 из 1"
    assert prompts.history_select_calls[0]["has_previous_page"] is False
    assert prompts.history_select_calls[0]["has_next_page"] is False
    output = console.export_text()
    assert "Журнал" not in output
    assert "Контекст журнала" not in output
    assert "Записи журнала" not in output
    assert "Последние локальные артефакты" not in output
    assert "run-001" not in output
    assert "Страница 1 из 1" not in output
    assert "1-1 из 1" not in output
    assert "data/artifacts" not in output
    assert "data/logs" not in output
    assert "Пропущенные записи" not in output
    assert "Пропущенные артефакты" not in output
    assert "Ручной запуск" not in output
    assert "fqdn-updater sync --config" not in output


def test_runs_menu_opens_selected_run_details_with_log_hint(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["run:0", "back", None])
    controller, console = _panel_controller(tmp_path, prompts=prompts)
    config = _config()
    artifacts_dir = tmp_path / "data" / "artifacts"
    run_history_service = _RecordingRunHistoryService(
        artifacts_dir=artifacts_dir,
        runs=(
            RecentRun(
                path=artifacts_dir / "run-001.json",
                artifact=_artifact(
                    run_id="run-001",
                    status=RunStatus.PARTIAL,
                    finished_at=datetime(2026, 4, 8, 13, 1, tzinfo=timezone.utc),
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
        ),
    )
    controller._load_config = lambda: config  # type: ignore[method-assign]
    controller._run_history_service = run_history_service  # type: ignore[method-assign]

    controller._runs_menu()

    assert run_history_service.calls == [
        ("data/artifacts", controller._config_path, 10, 0),
        ("data/artifacts", controller._config_path, 10, 0),
    ]
    assert prompts.history_select_calls[0]["message"] == "Журнал"
    assert prompts.history_select_calls[0]["choice_titles"] == [
        "dry_run   manual     partial  08.04.2026 13:01:00  Main router  изменено=1 ошибок=1",
    ]
    assert "run-001.json" not in prompts.history_select_calls[0]["choice_titles"][0]
    assert prompts.select_calls[0]["message"] == "Запись журнала"
    assert prompts.select_calls[0]["choice_titles"] == ["Назад к журналу"]
    output = console.export_text()
    assert "Запись журнала run-001.json" in output
    assert "Файл записи" in output
    assert "data/artifacts/run-001.json" in output
    assert "Полный лог" in output
    assert "data/logs/run.log" in output
    assert "Подсказка" in output
    assert "cat" in output
    assert str(tmp_path / "data" / "logs" / "run.log") in output
    assert "Ошибки" in output
    assert "Main router / youtube" in output
    assert "router rejected update" in output


def test_manual_run_menu_dry_run_choice_calls_orchestrator_and_renders_summary(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["dry-run"])
    controller, console = _panel_controller(tmp_path, prompts=prompts)
    config = _config()
    dry_run_result = _dry_run_result()
    dry_run_orchestrator = _RecordingDryRunOrchestrator(result=dry_run_result)
    controller._load_config = lambda: config  # type: ignore[method-assign]
    controller._dry_run_orchestrator = dry_run_orchestrator  # type: ignore[method-assign]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    controller._manual_run_menu()

    assert prompts.select_calls[0]["message"] == "Ручной запуск"
    assert prompts.select_calls[0]["choices"] == ["dry-run", "sync", "back"]
    assert len(dry_run_orchestrator.calls) == 1
    assert dry_run_orchestrator.calls[0][1] is RunTrigger.MANUAL
    output = console.export_text()
    assert "Dry-run: run_id=run-123 status=partial artifact=data/artifacts/run-123.json" in output
    assert "изменено=1 ошибок=1" in output
    assert "router-1" in output
    assert "partial" in output


def test_runs_menu_supports_pagination_with_next_and_previous_actions(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["next-page", "next-page", "prev-page", None])
    controller, console = _panel_controller(tmp_path, prompts=prompts)
    config = _config()
    artifacts_dir = tmp_path / "data" / "artifacts"
    runs = tuple(
        RecentRun(
            path=artifacts_dir / f"run-{minute:03d}.json",
            artifact=_artifact(
                run_id=f"run-{minute:03d}",
                status=RunStatus.SUCCESS,
                finished_at=datetime(2026, 4, 8, 13, minute, tzinfo=timezone.utc),
                service_results=(
                    ServiceRunResult(
                        service_key="telegram",
                        object_group_name="svc-telegram",
                        status=ServiceResultStatus.NO_CHANGES,
                        unchanged_count=5,
                    ),
                ),
            ),
        )
        for minute in range(24, -1, -1)
    )
    run_history_service = _RecordingRunHistoryService(artifacts_dir=artifacts_dir, runs=runs)
    clear_calls: list[bool] = []
    original_clear = console.clear

    def record_clear(*, home: bool = True) -> None:
        clear_calls.append(home)
        original_clear(home=home)

    console.clear = record_clear  # type: ignore[method-assign]
    controller._load_config = lambda: config  # type: ignore[method-assign]
    controller._run_history_service = run_history_service  # type: ignore[method-assign]
    controller._dry_run_orchestrator = _RecordingDryRunOrchestrator(result=_dry_run_result())  # type: ignore[method-assign]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    controller._runs_menu()

    assert run_history_service.calls == [
        ("data/artifacts", controller._config_path, 10, 0),
        ("data/artifacts", controller._config_path, 10, 10),
        ("data/artifacts", controller._config_path, 10, 20),
        ("data/artifacts", controller._config_path, 10, 10),
    ]
    assert clear_calls == []
    assert prompts.select_calls == []
    assert prompts.history_select_calls[0]["choices"] == [*(f"run:{index}" for index in range(10))]
    assert prompts.history_select_calls[0]["default"] == "run:0"
    assert prompts.history_select_calls[0]["page_label"] == "Страница 1 из 3"
    assert prompts.history_select_calls[0]["has_previous_page"] is False
    assert prompts.history_select_calls[0]["has_next_page"] is True
    assert prompts.history_select_calls[0]["choice_titles"][0] == (
        "dry_run   manual     success  08.04.2026 13:15:00  Main router  изменено=0 ошибок=0"
    )
    assert prompts.history_select_calls[0]["choice_titles"][9] == (
        "dry_run   manual     success  08.04.2026 13:24:00  Main router  изменено=0 ошибок=0"
    )
    assert prompts.history_select_calls[1]["choices"] == [*(f"run:{index}" for index in range(10))]
    assert prompts.history_select_calls[1]["page_label"] == "Страница 2 из 3"
    assert prompts.history_select_calls[1]["has_previous_page"] is True
    assert prompts.history_select_calls[1]["has_next_page"] is True
    assert prompts.history_select_calls[2]["choices"] == [*(f"run:{index}" for index in range(5))]
    assert prompts.history_select_calls[2]["page_label"] == "Страница 3 из 3"
    assert prompts.history_select_calls[2]["has_previous_page"] is True
    assert prompts.history_select_calls[2]["has_next_page"] is False
    assert prompts.history_select_calls[3]["choices"] == [*(f"run:{index}" for index in range(10))]
    assert prompts.history_select_calls[3]["page_label"] == "Страница 2 из 3"
    assert prompts.history_select_calls[3]["has_previous_page"] is True
    assert prompts.history_select_calls[3]["has_next_page"] is True
    output = console.export_text()
    assert "Страница 1 из 3" not in output
    assert "Страница 2 из 3" not in output
    assert "Страница 3 из 3" not in output
    assert "1-10 из 25" not in output
    assert "11-20 из 25" not in output
    assert "21-25 из 25" not in output


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
            "routers": [
                {
                    "id": "router-1",
                    "name": "Main router",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_SECRET",
                    "enabled": True,
                }
            ],
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
        started_at=datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc),
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


def _dry_run_result() -> DryRunExecutionResult:
    artifact = RunArtifact(
        run_id="run-123",
        trigger=RunTrigger.MANUAL,
        mode=RunMode.DRY_RUN,
        status=RunStatus.PARTIAL,
        started_at=datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 8, 13, 1, tzinfo=timezone.utc),
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
