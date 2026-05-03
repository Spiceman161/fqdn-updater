from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import fqdn_updater.cli.panel as panel_module
from fqdn_updater import __version__
from fqdn_updater.application.route_target_discovery import RouteTargetDiscoveryResult
from fqdn_updater.application.run_history import RecentRun, RunHistoryResult
from fqdn_updater.application.sync_orchestration import SyncExecutionResult
from fqdn_updater.cli import panel_formatting, panel_router_support, panel_schedule
from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.keenetic import RouteTargetCandidate
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
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
from fqdn_updater.domain.source_registry import builtin_service_definitions
from fqdn_updater.domain.status_diagnostics import (
    OverallDiagnosticStatus,
    RouterDiagnosticStatus,
    RouterStatusDiagnostic,
    StatusDiagnosticsResult,
)
from fqdn_updater.infrastructure.secret_env_file import (
    SecretEnvFile,
    password_env_key_for_router_id,
)
from fqdn_updater.infrastructure.service_count_cache import (
    ServiceCountCacheRepository,
    ServiceEntryCountSnapshot,
)
from fqdn_updater.infrastructure.systemd_scheduler import SystemdScheduleInstallResult

from .panel_test_support import (
    ScriptedPromptAdapter,
    ScriptedSourceLoadingService,
    make_panel_controller,
    make_source_load_report,
    write_config,
)


class _FakeDiscoveryService:
    def __init__(self, result: RouteTargetDiscoveryResult) -> None:
        self.result = result
        self.calls: list[str] = []
        self.routers: list[object] = []
        self.password_overrides: list[str | None] = []

    def discover_wireguard_targets(
        self,
        *,
        router,
        password_override: str | None = None,
    ) -> RouteTargetDiscoveryResult:
        self.calls.append(router.id)
        self.routers.append(router)
        self.password_overrides.append(password_override)
        return self.result


class _RecordingStatusService:
    def __init__(self, result: StatusDiagnosticsResult) -> None:
        self.result = result
        self.calls: list[AppConfig] = []

    def check(self, *, config: AppConfig) -> StatusDiagnosticsResult:
        self.calls.append(config)
        return self.result


class _RecordingSyncOrchestrator:
    def __init__(self, result: SyncExecutionResult) -> None:
        self.result = result
        self.calls: list[tuple[AppConfig, RunTrigger]] = []

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> SyncExecutionResult:
        self.calls.append((config, trigger))
        return self.result


class _RecordingRunHistoryService:
    def __init__(self, *, artifacts_dir: Path, runs: tuple[RecentRun, ...]) -> None:
        self.artifacts_dir = artifacts_dir
        self.runs = runs
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
            runs=self.runs,
            warnings=(),
            total_count=len(self.runs),
        )


def _sync_result(*, router_id: str = "router-1") -> SyncExecutionResult:
    timestamp = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    artifact = RunArtifact(
        run_id="run-sync",
        trigger=RunTrigger.MANUAL,
        mode=RunMode.APPLY,
        status=RunStatus.SUCCESS,
        started_at=timestamp,
        finished_at=timestamp,
        log_path=Path("data/logs/run-sync.log"),
        router_results=[
            RouterRunResult(
                router_id=router_id,
                status=RouterResultStatus.UPDATED,
                service_results=[
                    ServiceRunResult(
                        service_key="telegram",
                        object_group_name="fqdn-telegram",
                        status=ServiceResultStatus.UPDATED,
                        added_count=1,
                    )
                ],
            )
        ],
    )
    return SyncExecutionResult(
        artifact=artifact,
        artifact_path=Path("data/artifacts/run-sync.json"),
        plans=(),
    )


def test_main_menu_passes_dashboard_hint_lines_to_prompt(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["exit"])
    controller, console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)

    controller.run()

    output = console.export_text()
    assert f"FQDN-updater v{__version__}" in output
    assert panel_module.DONATION_LABEL not in output
    assert "Подсказка" not in output
    assert prompts.select_calls[0]["hint_lines"] == panel_module.MAIN_MENU_HINT_LINES
    assert prompts.select_calls[0]["hint_lines"] == (
        "Для начала работы добавьте маршрутизатор Keenetic с ОС версии 5 и выше.",
        "Затем настройте обновление списков по расписанию.",
    )


def test_panel_run_adds_missing_builtin_services_to_existing_config(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["exit"])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)

    controller.run()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    service_keys = [service["key"] for service in payload["services"]]
    assert service_keys[:5] == [
        "anime",
        "block",
        "block_p2p_streaming",
        "block_vpn_proxy_privacy",
        "block_dev_hosting_security",
    ]
    assert "geoblock_ai" in service_keys
    assert service_keys.index("news") < service_keys.index("cloudflare")


def test_dashboard_omits_old_workspace_and_services_column(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["exit"])
    controller, console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
        services=[
            {
                "key": "meta",
                "source_urls": ["https://example.com/meta.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            }
        ],
        mappings=[
            {
                "router_id": "router-1",
                "service_key": "meta",
                "object_group_name": "fqdn-meta",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            }
        ],
    )

    controller.run()

    output = console.export_text()
    assert "Рабочий контекст" not in output
    assert "Сервисы" not in output
    assert "meta (whatsapp)" not in output
    assert "Deployment root" not in output
    assert "Compose service" not in output


def test_dashboard_renders_router_last_run_columns_without_services_column(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["exit"])
    controller, console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            },
            {
                "id": "router-2",
                "name": "Router 2",
                "rci_url": "https://router-2.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_TWO_SECRET",
                "enabled": False,
            },
        ],
    )
    artifacts_dir = tmp_path / "data" / "artifacts"
    run_history_service = _RecordingRunHistoryService(
        artifacts_dir=artifacts_dir,
        runs=(
            RecentRun(
                path=artifacts_dir / "run-001.json",
                artifact=RunArtifact(
                    run_id="run-001",
                    trigger=RunTrigger.MANUAL,
                    mode=RunMode.APPLY,
                    status=RunStatus.PARTIAL,
                    started_at=datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
                    finished_at=datetime(2026, 4, 25, 12, 3, tzinfo=timezone.utc),
                    log_path=Path("data/logs/run-001.log"),
                    router_results=[
                        RouterRunResult(
                            router_id="router-1",
                            status=RouterResultStatus.UPDATED,
                        ),
                        RouterRunResult(
                            router_id="router-2",
                            status=RouterResultStatus.FAILED,
                            error_message="router rejected update",
                        ),
                    ],
                ),
            ),
        ),
    )
    controller._run_history_service = run_history_service  # type: ignore[method-assign]

    controller.run()

    output = console.export_text()
    assert run_history_service.calls == [("data/artifacts", controller._config_path, 50, 0)]
    assert "Сервисы" not in output
    assert "Последний запуск" in output
    assert "25.04.2026 12:03" in output
    assert "ok" in output
    assert "fail" in output


def test_support_menu_renders_sbp_and_ton_donation_methods(tmp_path) -> None:
    prompts = ScriptedPromptAdapter()
    controller, console = make_panel_controller(tmp_path, prompts=prompts)

    controller._support_menu()

    output = console.export_text()
    assert panel_module.DONATION_LABEL in output
    assert "Спасибо за поддержку проекта 🚀" in output
    assert "Спасибо за поддержку проекта и LLM-подписок" not in output
    assert "Перевод СБП" in output
    assert "Ссылка на СБП:" in output
    assert "QR-код ниже можно отсканировать камерой" not in output
    for chunk in panel_module._donation_url_chunks(panel_module.DONATION_URL):
        assert chunk in output
    assert "Перевод TON" in output
    assert "Адрес TON:" in output
    assert panel_module.TON_DONATION_ADDRESS in output
    assert "🤖" in output
    assert "☕" in output
    assert "✨" in output
    for value in (panel_module.DONATION_URL, panel_module.TON_DONATION_QR_VALUE):
        qr_lines = panel_module._donation_qr_lines(value)
        assert len(qr_lines) <= 20
        qr_width = max(len(line) for line in qr_lines)
        assert abs(qr_width - len(qr_lines) * 2) <= 1
    assert any(character in output for character in ("█", "▀", "▄"))
    assert prompts.pause_messages == ["Нажмите любую клавишу для продолжения..."]


def test_main_menu_includes_manual_run_and_schedule_sections(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["exit"])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)

    controller.run()

    choice_titles = prompts.select_calls[0]["choice_titles"]
    assert "🚀 Ручной запуск" in choice_titles
    assert "⏱ Расписание" in choice_titles
    assert "💜 Поддержать проект" in choice_titles
    assert choice_titles[choice_titles.index("✅ Проверка конфига") + 1] == ("💜 Поддержать проект")


def test_router_menu_passes_hint_lines_to_prompt(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["back"])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
    )

    controller._router_menu()

    assert prompts.select_calls[0]["message"] == "Маршрутизаторы"
    assert prompts.select_calls[0]["hint_lines"] == panel_module.ROUTER_MENU_HINT_LINES
    assert prompts.select_calls[0]["choice_titles"][-1] == "↩ Главное меню"


def test_schedule_menu_passes_hint_lines_to_prompt(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["back"])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)

    controller._schedule_menu()

    assert prompts.select_calls[0]["message"] == "Расписание"
    assert prompts.select_calls[0]["hint_lines"] == panel_schedule.SCHEDULE_MENU_HINT_LINES
    assert prompts.select_calls[0]["choice_titles"][-1] == "↩ Главное меню"


def test_about_menu_describes_project_scope_and_source_repository(tmp_path) -> None:
    prompts = ScriptedPromptAdapter()
    controller, console = make_panel_controller(tmp_path, prompts=prompts)

    controller._about_menu()

    output = console.export_text()
    assert "FQDN-updater panel" in output
    assert "Keenetic (Netcraze)" in output
    assert "https://github.com/itdoginfo/allow-domains" in output
    assert "поддерживает эти списки в актуальном состоянии" in output
    assert "ручной запуск" in output
    assert prompts.pause_messages == ["Нажмите любую клавишу для продолжения..."]


def test_manual_run_menu_selects_routers_and_runs_sync(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["sync"],
        checkbox_answers=[["router-1", "router-2"]],
    )
    controller, console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            },
            {
                "id": "router-2",
                "name": "Router 2",
                "rci_url": "https://router-2.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_TWO_SECRET",
                "enabled": False,
            },
        ],
        mappings=[
            {
                "router_id": "router-1",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            },
            {
                "router_id": "router-2",
                "service_key": "youtube",
                "object_group_name": "fqdn-youtube",
                "route_target_type": "interface",
                "route_target_value": "Wireguard9",
                "managed": True,
            },
        ],
    )
    sync_orchestrator = _RecordingSyncOrchestrator(result=_sync_result())
    controller._sync_orchestrator = sync_orchestrator  # type: ignore[attr-defined]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    controller._manual_run_menu()

    assert prompts.select_calls[0]["message"] == "Ручной запуск"
    assert prompts.select_calls[0]["choices"] == ["dry-run", "sync", "back"]
    assert prompts.select_calls[0]["choice_titles"] == [
        "🧪 Dry-run (тестовый запуск без изменения списков)",
        "🚀 Sync (применить изменения в Keenetic)",
        "↩ Главное меню",
    ]
    assert prompts.select_calls[0]["hint_lines"] == panel_module.MANUAL_RUN_HINT_LINES
    checkbox_call = prompts.checkbox_calls[0]
    assert checkbox_call["message"] == "Ручной запуск"
    assert checkbox_call["hint_lines"] == panel_module.MANUAL_RUN_HINT_LINES
    assert checkbox_call["instruction"] == (
        "Стрелки выбирают, Пробел отмечает, Enter запускает, Esc возвращает назад."
    )
    assert checkbox_call["table_summary"] == "Будет запущено: 1"
    assert checkbox_call["choices"][0]["checked"] is True
    assert checkbox_call["choices"][1]["checked"] is False
    assert len(sync_orchestrator.calls) == 1
    sync_config, trigger = sync_orchestrator.calls[0]
    assert trigger is RunTrigger.MANUAL
    assert [router.id for router in sync_config.routers] == ["router-1", "router-2"]
    assert {mapping.router_id for mapping in sync_config.mappings} == {"router-1", "router-2"}
    assert all(Path(path).is_absolute() for path in (sync_config.runtime.artifacts_dir,))

    output = console.export_text()
    assert "Sync: run_id=run-sync status=success artifact=data/artifacts/run-sync.json" in output


def test_schedule_menu_saves_daily_schedule_and_systemd_defaults(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["edit", "daily", "back"],
        text_answers=[
            "03:15, 12:00",
            "Europe/Moscow",
            "fqdn-updater",
            "/opt/fqdn-updater",
            "fqdn-updater",
        ],
        confirm_answers=[True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)

    controller._schedule_menu()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["runtime"]["schedule"] == {
        "mode": "daily",
        "times": ["03:15", "12:00"],
        "timezone": "Europe/Moscow",
        "weekdays": [],
        "systemd": {
            "compose_service": "fqdn-updater",
            "deployment_root": "/opt/fqdn-updater",
            "unit_name": "fqdn-updater",
        },
    }


def test_schedule_menu_install_action_calls_installer_and_renders_result(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["install", "back"])
    controller, console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    install_calls: list[tuple[AppConfig, str]] = []

    class _RecordingInstaller:
        def install(self, *, config: AppConfig, config_path):
            install_calls.append((config, str(config_path)))
            return SystemdScheduleInstallResult(
                service_path=Path("/etc/systemd/system/fqdn-updater.service"),
                timer_path=Path("/etc/systemd/system/fqdn-updater.timer"),
                timer_action="started",
            )

    controller._schedule_installer = _RecordingInstaller()  # type: ignore[attr-defined]

    controller._schedule_menu()

    output = console.export_text()
    assert install_calls[0][1] == str(controller._config_path)
    assert "systemd units обновлены" in output
    assert "timer_action=started" in output


def test_router_menu_uses_shorter_edit_label(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["back"])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
    )

    controller._router_menu()

    assert prompts.select_calls[0]["choice_titles"][1] == "✏ Изменить параметры маршрутизатора"
    assert "Повернуть пароль RCI" not in prompts.select_calls[0]["choice_titles"]


def test_router_menu_status_choice_calls_diagnostics_service_and_renders_router_details(
    tmp_path,
) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["status", "back"])
    controller, console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
    )
    raw_error = "upstream rejected request " + ("x" * 600)
    status_service = _RecordingStatusService(
        result=StatusDiagnosticsResult(
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
                    error_message=raw_error,
                ),
            ),
        )
    )
    controller._status_service = status_service  # type: ignore[attr-defined]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    controller._router_menu()

    assert len(status_service.calls) == 1
    assert prompts.pause_messages == ["Нажмите любую клавишу для продолжения..."]
    assert prompts.select_calls[0]["choice_titles"][3] == "🔎 Проверка связи с маршрутизаторами"
    plain_output = console.export_text(clear=False)
    styled_output = console.export_text(styles=True, clear=False)
    assert "Status diagnostics: overall=failed checked=3" in plain_output
    assert "router-healthy" in plain_output
    assert "router-degraded" in plain_output
    assert "router-failed" in plain_output
    assert "dns proxy disabled" in plain_output
    assert "upstream rejected request" in plain_output
    assert ("x" * 400) not in plain_output
    assert "dns proxy disabled" in styled_output
    assert "\x1b[" in styled_output


def test_add_router_passes_hint_lines_through_wizard_steps(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Router 1",
            "api_updater",
            "https://router-1.example/rci/",
            "Wireguard0",
        ],
        checkbox_answers=[["telegram", "google_ai"]],
        select_answers=[],
        confirm_answers=[True, False, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    generated_password = "Aa1!bcdefghijklmnopq"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._add_router()

    assert prompts.text_calls[0]["hint_lines"] == panel_router_support.ADD_ROUTER_HINT_LINES
    assert prompts.text_calls[1]["message"] == "RCI username"
    assert (
        prompts.text_calls[1]["hint_lines"] == panel_router_support.ADD_ROUTER_USERNAME_HINT_LINES
    )
    assert prompts.text_calls[2]["hint_lines"] == panel_router_support.ADD_ROUTER_RCI_URL_HINT_LINES
    assert (
        prompts.text_calls[3]["hint_lines"] == panel_router_support.BASE_ROUTE_INTERFACE_HINT_LINES
    )
    assert (
        prompts.checkbox_calls[0]["hint_lines"] == panel_router_support.SERVICE_SELECTION_HINT_LINES
    )
    assert prompts.text_calls[3]["message"] == "Базовый интерфейс маршрутизации"
    assert prompts.select_calls == []
    assert (
        prompts.confirm_calls[0]["hint_lines"]
        == panel_router_support.ADD_ROUTER_PASSWORD_HINT_LINES
    )
    assert prompts.confirm_calls[1]["message"] == "Использовать отдельный маршрут для google_ai?"
    assert (
        prompts.confirm_calls[1]["hint_lines"] == panel_router_support.GOOGLE_AI_OVERRIDE_HINT_LINES
    )
    assert (
        prompts.confirm_calls[-1]["hint_lines"] == panel_router_support.ADD_ROUTER_SAVE_HINT_LINES
    )


def test_password_confirmation_hint_mentions_access_checkbox_and_save() -> None:
    assert (
        "Поставьте галочку в столбце «Доступ» напротив нового пользователя и сохраните подключение."
        in panel_router_support.ADD_ROUTER_PASSWORD_HINT_LINES
    )


def test_add_router_save_hint_mentions_review_and_confirm() -> None:
    assert panel_router_support.ADD_ROUTER_SAVE_HINT_LINES == (
        "Проверьте введенные данные и подтвердите сохранение маршрутизатора.",
    )


def test_add_router_uses_requested_default_service_selection(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Router 1",
            "api_updater",
            "https://router-1.example/rci/",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        services=[service.model_dump(mode="json") for service in builtin_service_definitions()],
    )
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: "Aa1!bcdefghijklmnopq"
    )

    controller._add_router()

    checked_service_keys = {
        choice["value"] for choice in prompts.checkbox_calls[0]["choices"] if choice["checked"]
    }
    assert checked_service_keys == {
        "block_vpn_proxy_privacy",
        "block_news_politics",
        "block_other",
        "geoblock_ai",
        "geoblock_other",
        "hodca_network_os_tools",
        "hodca_ai_education_research",
        "hodca_other",
        "news",
        "cloudflare",
        "cloudfront",
        "digitalocean",
        "discord",
        "google_ai",
        "hdrezka",
        "hetzner",
        "meta",
        "ovh",
        "roblox",
        "telegram",
        "tiktok",
        "twitter",
        "youtube",
    }
    checked_by_key = {
        choice["value"]: choice["checked"] for choice in prompts.checkbox_calls[0]["choices"]
    }
    assert checked_by_key["block"] is False
    assert checked_by_key["geoblock"] is False
    assert checked_by_key["hodca"] is False
    assert checked_by_key["anime"] is False
    assert checked_by_key["porn"] is False


def test_add_router_service_selection_uses_source_counts_and_fixed_hint_lines(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Router 1",
            "api_updater",
            "https://router-1.example/rci/",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    generated_password = "Aa1!bcdefghijklmnopq"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )
    controller._source_loading_service = ScriptedSourceLoadingService(  # type: ignore[attr-defined]
        make_source_load_report(
            loaded={
                "telegram": (
                    ObjectGroupEntry.from_domain("one.example"),
                    ObjectGroupEntry.from_network("192.0.2.0/24"),
                    ObjectGroupEntry.from_network("2001:db8::/32"),
                ),
                "google_ai": (
                    ObjectGroupEntry.from_network("198.51.100.0/24"),
                    ObjectGroupEntry.from_network("198.51.101.0/24"),
                ),
                "youtube": (
                    ObjectGroupEntry.from_domain("youtube.example"),
                    ObjectGroupEntry.from_domain("example.org"),
                    ObjectGroupEntry.from_network("2001:db8:1::/48"),
                ),
            }
        )
    )

    controller._add_router()

    assert controller._source_loading_service.calls == [  # type: ignore[attr-defined]
        ["telegram", "google_ai", "youtube"]
    ]
    checkbox_call = prompts.checkbox_calls[0]
    assert checkbox_call["hint_lines"] == panel_router_support.SERVICE_SELECTION_HINT_LINES
    assert checkbox_call["hint_lines"] == (
        (
            "Для каждого выбранного сервиса будет создан свой список в разделе "
            "«Маршрутизация» Keenetic."
        ),
        "Лимит доменов роутеров Keenetic составляет 1024 записи. "
        "Вам необходимо выбрать не более этого количества записей.",
        "Для IPv4+IPv6 действует отдельный лимит: около 4000 subnet-записей суммарно на роутер.",
    )
    assert checkbox_call["table_header"] == panel_formatting._service_selection_header()
    assert checkbox_call["table_summary"] == (f"{'Итого выбрано':<22} | {3:>7} | {3:>7} | {2:>7}")
    assert [choice["value"] for choice in checkbox_call["choices"]] == [
        "telegram",
        "google_ai",
        "youtube",
    ]
    assert checkbox_call["choices"][0]["title"].endswith("|       1 |       1 |       1")
    assert checkbox_call["choices"][1]["title"].endswith("|       0 |       2 |       0")
    assert checkbox_call["choices"][2]["title"].endswith("|       2 |       0 |       1")


def test_service_selection_groups_composite_services_and_collapses_full_selection(
    tmp_path,
) -> None:
    prompts = ScriptedPromptAdapter(
        checkbox_answers=[
            [
                "block",
                "block_p2p_streaming",
                "block_vpn_proxy_privacy",
                "block_dev_hosting_security",
                "block_finance_shopping",
                "block_social_creators",
                "block_news_politics",
                "block_other",
                "hodca",
                "hodca_dev_cloud_saas",
                "hodca_network_os_tools",
                "hodca_media_games",
                "hodca_ai_education_research",
                "hodca_social_lifestyle",
                "hodca_finance_shopping",
                "hodca_other",
            ]
        ]
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    service_keys = [
        "block",
        "block_p2p_streaming",
        "block_vpn_proxy_privacy",
        "block_dev_hosting_security",
        "block_finance_shopping",
        "block_social_creators",
        "block_news_politics",
        "block_other",
        "hodca",
        "hodca_dev_cloud_saas",
        "hodca_network_os_tools",
        "hodca_media_games",
        "hodca_ai_education_research",
        "hodca_social_lifestyle",
        "hodca_finance_shopping",
        "hodca_other",
    ]
    write_config(
        controller._config_path,
        services=[
            {
                "key": service_key,
                "source_urls": [f"https://example.com/{service_key}.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            }
            for service_key in service_keys
        ],
    )

    selected = controller._prompt_service_selection(
        config=controller._load_config(),
        selected=set(),
    )

    assert selected == {"block", "hodca"}
    checkbox_call = prompts.checkbox_calls[0]
    assert (
        checkbox_call["selection_groups"]["block"]
        == panel_formatting.SERVICE_SELECTION_GROUPS["block"]
    )
    assert (
        checkbox_call["selection_groups"]["hodca"]
        == panel_formatting.SERVICE_SELECTION_GROUPS["hodca"]
    )
    choice_titles = [choice["title"] for choice in checkbox_call["choices"]]
    assert choice_titles[0].startswith("block (full)")
    assert choice_titles[1].startswith("   p2p/media")
    assert choice_titles[8].startswith("H.O.D.C.A (full)")
    assert choice_titles[9].startswith("   dev/cloud/SaaS")


def test_service_selection_totals_marks_domain_limit_overflow_red() -> None:
    summary = panel_formatting._service_selection_totals_line(
        selected_values=("large",),
        service_counts={"large": panel_formatting.ServiceEntryCounts(domains=1025, ipv4=0, ipv6=0)},
    )

    assert isinstance(summary, list)
    assert ("fg:#ff5f5f bold", "   1025") in summary
    assert "".join(text for _style, text in summary) == (
        f"{'Итого выбрано':<22} | {1025:>7} | {0:>7} | {0:>7}"
    )


def test_add_router_service_selection_renders_meta_as_meta_whatsapp(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Router 1",
            "api_updater",
            "https://router-1.example/rci/",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        services=[
            {
                "key": "meta",
                "source_urls": ["https://example.com/meta.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            }
        ],
    )
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: "Aa1!bcdefghijklmnopq"
    )

    controller._add_router()

    checkbox_call = prompts.checkbox_calls[0]
    assert checkbox_call["choices"][0]["value"] == "meta"
    assert checkbox_call["choices"][0]["title"].startswith("meta (whatsapp)")


def test_add_router_service_selection_uses_cached_counts_without_source_reload(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Router 1",
            "api_updater",
            "https://router-1.example/rci/",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    generated_password = "Aa1!bcdefghijklmnopq"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )
    source_loading_service = ScriptedSourceLoadingService(make_source_load_report(loaded={}))
    controller._source_loading_service = source_loading_service  # type: ignore[attr-defined]
    cache_path = controller._service_count_cache_path(config=controller._load_config())  # type: ignore[attr-defined]
    ServiceCountCacheRepository().write(
        path=cache_path,
        counts={
            "telegram": ServiceEntryCountSnapshot(domains=101, ipv4=3, ipv6=1),
            "google_ai": ServiceEntryCountSnapshot(domains=8, ipv4=11, ipv6=0),
            "youtube": ServiceEntryCountSnapshot(domains=22, ipv4=0, ipv6=4),
        },
    )

    controller._add_router()

    assert source_loading_service.calls == []
    checkbox_call = prompts.checkbox_calls[0]
    assert checkbox_call["choices"][0]["title"].endswith("|     101 |       3 |       1")
    assert checkbox_call["choices"][1]["title"].endswith("|       8 |      11 |       0")
    assert checkbox_call["choices"][2]["title"].endswith("|      22 |       0 |       4")


def test_add_router_service_selection_refreshes_incomplete_cached_counts(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Router 1",
            "api_updater",
            "https://router-1.example/rci/",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        services=[
            {
                "key": "telegram",
                "source_urls": ["https://example.com/telegram.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            },
            {
                "key": "block",
                "source_urls": ["https://example.com/block.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            },
        ],
    )
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: "Aa1!bcdefghijklmnopq"
    )
    source_loading_service = ScriptedSourceLoadingService(
        make_source_load_report(
            loaded={
                "block": (
                    ObjectGroupEntry.from_domain("one.example"),
                    ObjectGroupEntry.from_domain("two.example"),
                ),
            }
        )
    )
    controller._source_loading_service = source_loading_service  # type: ignore[attr-defined]
    cache_path = controller._service_count_cache_path(  # type: ignore[attr-defined]
        config=controller._load_config()  # type: ignore[attr-defined]
    )
    ServiceCountCacheRepository().write(
        path=cache_path,
        counts={
            "telegram": ServiceEntryCountSnapshot(domains=101, ipv4=3, ipv6=1),
        },
    )

    controller._add_router()

    assert source_loading_service.calls == [["telegram", "block"]]
    checkbox_call = prompts.checkbox_calls[0]
    assert checkbox_call["choices"][0]["title"].endswith("|     101 |       3 |       1")
    assert checkbox_call["choices"][1]["title"].endswith("|       2 |       0 |       0")


def test_add_router_creates_config_secret_and_default_mappings(tmp_path, monkeypatch) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Router 1",
            "api_updater",
            "https://router-1.example/rci/",
            "Wireguard0",
        ],
        checkbox_answers=[["telegram", "google_ai"]],
        select_answers=[],
        confirm_answers=[True, False, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    generated_password = "Aa1!bcdefghijklmnopq"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._add_router()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"] == [
        {
            "allowed_source_ips": [],
            "auth_method": "digest",
            "enabled": True,
            "id": "router-1",
            "name": "Router 1",
            "password_env": "FQDN_UPDATER_ROUTER_ROUTER_1_PASSWORD",
            "password_file": None,
            "rci_url": "https://router-1.example/rci/",
            "tags": [],
            "timeout_seconds": 30,
            "username": "api_updater",
        }
    ]
    assert payload["mappings"] == [
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-google_ai",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "router_id": "router-1",
            "service_key": "google_ai",
        },
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-telegram",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "router_id": "router-1",
            "service_key": "telegram",
        },
    ]
    secret_env = SecretEnvFile(path=tmp_path / ".env.secrets")
    assert secret_env.read() == {
        "FQDN_UPDATER_ROUTER_ROUTER_1_PASSWORD": generated_password,
    }
    assert generated_password not in controller._config_path.read_text(encoding="utf-8")


def test_add_router_shows_generated_password_before_save_summary(tmp_path, monkeypatch) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Router 1",
            "api_updater",
            "https://router-1.example/rci/",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True, False],
    )
    controller, console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    generated_password = "Aa1!bcdefghijklmnopq"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._add_router()

    output = console.export_text()
    assert "Новый пароль RCI" in output
    assert "Username" in output
    assert generated_password in output
    assert "Проверка сохранения" in output
    assert output.index("Новый пароль RCI") < output.index("Проверка сохранения")


def test_add_router_generates_transliterated_id_from_cyrillic_name(tmp_path, monkeypatch) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Тестовый маршрутизатор",
            "api-user",
            "https://main.example/rci/",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    monkeypatch.setattr(panel_module.RciPasswordGenerator, "generate", lambda self: "unused")

    controller._add_router()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"][-1]["id"] == "testovyi-marshrutizator"
    assert payload["routers"][-1]["password_env"] == (
        "FQDN_UPDATER_ROUTER_TESTOVYI_MARSHRUTIZATOR_PASSWORD"
    )


def test_edit_router_updates_password_and_preserves_existing_mappings(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["router-1"],
        text_answers=[
            "Router One Renamed",
            "https://router-1-renamed.example/rci/",
            "api_updater",
        ],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
        mappings=[
            {
                "router_id": "router-1",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            }
        ],
    )
    secret_path = tmp_path / ".env.secrets"
    secret_path.write_text("ROUTER_ONE_SECRET=old-secret\n", encoding="utf-8")
    generated_password = "Bb2@cdefghijklmnopqr"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._edit_router()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"][0]["name"] == "Router One Renamed"
    assert payload["routers"][0]["password_env"] == "ROUTER_ONE_SECRET"
    assert payload["routers"][0]["timeout_seconds"] == 30
    assert payload["mappings"] == [
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-telegram",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "router_id": "router-1",
            "service_key": "telegram",
        }
    ]
    assert SecretEnvFile(path=secret_path).read() == {"ROUTER_ONE_SECRET": generated_password}
    assert generated_password not in controller._config_path.read_text(encoding="utf-8")
    assert prompts.confirm_calls[0]["message"] == "Пароль уже обновлён у пользователя Keenetic?"
    assert (
        prompts.confirm_calls[0]["hint_lines"]
        == panel_router_support.EDIT_ROUTER_PASSWORD_HINT_LINES
    )


def test_edit_router_keeps_existing_password_when_user_does_not_update_it(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["router-1"],
        text_answers=[
            "Router One Renamed",
            "https://router-1-renamed.example/rci/",
            "api_updater",
        ],
        confirm_answers=[False, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
    )
    secret_path = tmp_path / ".env.secrets"
    secret_path.write_text("ROUTER_ONE_SECRET=old-secret\n", encoding="utf-8")
    generated_password = "KeepOldPassword123!"
    fake_service = _FakeDiscoveryService(
        RouteTargetDiscoveryResult(router_id="router-1", candidates=())
    )
    controller._route_target_discovery_service = fake_service  # type: ignore[attr-defined]
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._edit_router()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"][0]["name"] == "Router One Renamed"
    assert payload["routers"][0]["password_env"] == "ROUTER_ONE_SECRET"
    assert payload["routers"][0]["password_file"] is None
    assert SecretEnvFile(path=secret_path).read() == {"ROUTER_ONE_SECRET": "old-secret"}
    assert fake_service.password_overrides == [None]
    assert prompts.confirm_calls[1]["message"] == "Сохранить изменения маршрутизатора?"


def test_edit_router_shows_generated_password_before_save_summary(tmp_path, monkeypatch) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["router-1"],
        text_answers=[
            "Router 1",
            "https://router-1.example/rci/",
            "api-user",
        ],
        confirm_answers=[True, False],
    )
    controller, console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
    )
    generated_password = "Cc3#defghijklmnopqrs"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._edit_router()

    output = console.export_text()
    assert "Новый пароль RCI" in output
    assert "Password env" in output
    assert generated_password in output
    assert "Проверка сохранения" in output
    assert output.index("Новый пароль RCI") < output.index("Проверка сохранения")


def test_edit_router_checks_connectivity_with_draft_router_before_save(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["router-1"],
        text_answers=[
            "Router One Renamed",
            "https://router-1-renamed.example/rci/",
            "api-updater-new",
        ],
        confirm_answers=[True, False],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
    )
    generated_password = "Dd4$efghijklmnopqrst"
    fake_service = _FakeDiscoveryService(
        RouteTargetDiscoveryResult(router_id="router-1", candidates=())
    )
    controller._route_target_discovery_service = fake_service  # type: ignore[attr-defined]
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._edit_router()

    assert fake_service.calls == ["router-1"]
    draft_router = fake_service.routers[0]
    assert draft_router.id == "router-1"
    assert draft_router.name == "Router One Renamed"
    assert str(draft_router.rci_url) == "https://router-1-renamed.example/rci/"
    assert draft_router.username == "api-updater-new"
    assert draft_router.password_env == "ROUTER_ONE_SECRET"
    assert draft_router.timeout_seconds == 30
    assert fake_service.password_overrides == [generated_password]
    assert prompts.confirm_calls[1]["message"] == "Сохранить изменения маршрутизатора?"


def test_edit_router_reports_connectivity_error_but_still_allows_save(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["router-1"],
        text_answers=[
            "Router One Renamed",
            "https://router-1-renamed.example/rci/",
            "api_updater",
        ],
        confirm_answers=[True, True],
    )
    controller, console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
    )
    secret_path = tmp_path / ".env.secrets"
    secret_path.write_text("ROUTER_ONE_SECRET=old-secret\n", encoding="utf-8")
    generated_password = "Ee5%fghijklmnopqrstu"
    controller._route_target_discovery_service = _FakeDiscoveryService(  # type: ignore[attr-defined]
        RouteTargetDiscoveryResult(router_id="router-1", error_message="temporary failure")
    )
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._edit_router()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"][0]["name"] == "Router One Renamed"
    assert SecretEnvFile(path=secret_path).read() == {"ROUTER_ONE_SECRET": generated_password}
    styled_output = console.export_text(styles=True, clear=False)
    plain_output = console.export_text(clear=False)
    assert "Проверка связи с маршрутизатором не прошла." in plain_output
    assert "WireGuard discovery не прошёл: temporary failure" in plain_output
    assert "Проверка связи с маршрутизатором не прошла." in styled_output
    assert "\x1b[" in styled_output


def test_edit_router_selection_aligns_router_columns(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=[None])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "main",
                "name": "Тестовый роутер",
                "rci_url": "https://main.example/rci/",
                "username": "api-user",
                "password_env": "MAIN_SECRET",
                "enabled": True,
            },
            {
                "id": "main-2",
                "name": "main",
                "rci_url": "https://main-2.example/rci/",
                "username": "api-user",
                "password_env": "MAIN_2_SECRET",
                "enabled": True,
            },
        ],
    )

    controller._edit_router()

    choice_titles = prompts.select_calls[0]["choice_titles"]
    assert [[part.strip() for part in title.split("|")] for title in choice_titles] == [
        ["main", "Тестовый роутер", "включён"],
        ["main-2", "main", "включён"],
        ["Назад"],
    ]
    router_choice_titles = choice_titles[:2]
    assert [title.index("|") for title in router_choice_titles] == [7, 7]
    assert [title.rindex("|") for title in router_choice_titles] == [25, 25]


def test_toggle_router_enabled_preserves_existing_mappings(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(checkbox_answers=[[]])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
        mappings=[
            {
                "router_id": "router-1",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            }
        ],
    )

    controller._toggle_router_enabled()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"][0]["enabled"] is False
    assert payload["mappings"][0]["service_key"] == "telegram"


def test_toggle_router_enabled_uses_checkbox_table_and_preserves_checked_state(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(checkbox_answers=[["main-2"]])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "main",
                "name": "Городовиковск",
                "rci_url": "https://main.example/rci/",
                "username": "api-user",
                "password_env": "MAIN_SECRET",
                "enabled": True,
            },
            {
                "id": "main-2",
                "name": "main",
                "rci_url": "https://main-2.example/rci/",
                "username": "api-user",
                "password_env": "MAIN_2_SECRET",
                "enabled": False,
            },
        ],
    )
    initial_config = controller._load_config()
    router_id_width, router_name_width = panel_formatting._router_selection_column_widths(
        initial_config.routers
    )
    expected_titles = [
        panel_formatting._router_toggle_title(
            router=router,
            router_id_width=router_id_width,
            router_name_width=router_name_width,
        )
        for router in initial_config.routers
    ]

    controller._toggle_router_enabled()

    checkbox_call = prompts.checkbox_calls[0]
    assert checkbox_call["message"] == "Выберите маршрутизаторы, которые должны быть включены"
    assert checkbox_call["instruction"] == (
        "Стрелки выбирают, Пробел включает или выключает, Enter сохраняет, Esc назад."
    )
    assert checkbox_call["table_header"] == panel_formatting._router_toggle_header(
        router_id_width=router_id_width,
        router_name_width=router_name_width,
    )
    assert checkbox_call["table_summary"] == "Будет включено: 1 | выключено: 1"
    assert checkbox_call["choices"] == [
        {
            "title": expected_titles[0],
            "value": "main",
            "checked": True,
        },
        {
            "title": expected_titles[1],
            "value": "main-2",
            "checked": False,
        },
    ]

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert [router["enabled"] for router in payload["routers"]] == [False, True]


def test_lists_menu_updates_services_and_route_targets_preserving_disabled_mappings(
    tmp_path,
) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["router-1", "Wireguard7", "back"],
        checkbox_answers=[["telegram", "google_ai", "youtube"]],
        text_answers=[],
        confirm_answers=[False, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
        services=[
            {
                "key": "telegram",
                "source_urls": ["https://example.com/telegram.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            },
            {
                "key": "google_ai",
                "source_urls": ["https://example.com/google-ai.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            },
            {
                "key": "youtube",
                "source_urls": ["https://example.com/youtube.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            },
            {
                "key": "discord",
                "source_urls": ["https://example.com/discord.lst"],
                "format": "raw_domain_list",
                "enabled": False,
            },
        ],
        mappings=[
            {
                "router_id": "router-1",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            },
            {
                "router_id": "router-1",
                "service_key": "google_ai",
                "object_group_name": "fqdn-google_ai",
                "route_target_type": "interface",
                "route_target_value": "Wireguard1",
                "managed": True,
            },
            {
                "router_id": "router-1",
                "service_key": "discord",
                "object_group_name": "fqdn-discord",
                "route_target_type": "interface",
                "route_target_value": "Wireguard3",
                "managed": True,
            },
        ],
    )
    SecretEnvFile(path=tmp_path / ".env.secrets").write_value(
        key="ROUTER_ONE_SECRET",
        value="existing-secret",
    )
    controller._route_target_discovery_service = _FakeDiscoveryService(  # type: ignore[attr-defined]
        RouteTargetDiscoveryResult(
            router_id="router-1",
            candidates=(
                RouteTargetCandidate(
                    value="Wireguard7",
                    display_name="Wireguard7",
                    status="up",
                    detail="type=Wireguard",
                    connected=True,
                ),
            ),
        )
    )

    controller._lists_menu()

    assert prompts.select_calls[0]["message"] == "Выберите маршрутизатор для списков и маршрутов"
    assert prompts.select_calls[0]["choice_titles"][-1] == "Главное меню"
    assert prompts.select_calls[1]["message"] == "Базовый интерфейс маршрутизации"
    assert prompts.select_calls[1]["choice_titles"][-1] == "↩ Назад"
    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    mappings = sorted(payload["mappings"], key=lambda item: item["service_key"])
    assert mappings == [
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-discord",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard3",
            "router_id": "router-1",
            "service_key": "discord",
        },
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-google_ai",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard7",
            "router_id": "router-1",
            "service_key": "google_ai",
        },
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-telegram",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard7",
            "router_id": "router-1",
            "service_key": "telegram",
        },
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-youtube",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard7",
            "router_id": "router-1",
            "service_key": "youtube",
        },
    ]


def test_lists_menu_can_run_sync_for_selected_router_after_save(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["router-1", "Wireguard7", "sync-router"],
        checkbox_answers=[["telegram"]],
        confirm_answers=[True],
    )
    controller, console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            },
            {
                "id": "router-2",
                "name": "Router 2",
                "rci_url": "https://router-2.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_TWO_SECRET",
                "enabled": True,
            },
        ],
        services=[
            {
                "key": "telegram",
                "source_urls": ["https://example.com/telegram.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            },
        ],
        mappings=[
            {
                "router_id": "router-1",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            },
            {
                "router_id": "router-2",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard9",
                "managed": True,
            },
        ],
    )
    SecretEnvFile(path=tmp_path / ".env.secrets").write_value(
        key="ROUTER_ONE_SECRET",
        value="existing-secret",
    )
    controller._route_target_discovery_service = _FakeDiscoveryService(  # type: ignore[attr-defined]
        RouteTargetDiscoveryResult(
            router_id="router-1",
            candidates=(
                RouteTargetCandidate(
                    value="Wireguard7",
                    display_name="Wireguard7",
                    status="up",
                    detail="type=Wireguard",
                    connected=True,
                ),
            ),
        )
    )
    sync_orchestrator = _RecordingSyncOrchestrator(result=_sync_result())
    controller._sync_orchestrator = sync_orchestrator  # type: ignore[attr-defined]
    controller._load_runtime_secret_env_file = lambda *, config: None  # type: ignore[method-assign]

    controller._lists_menu()

    assert prompts.select_calls[-1]["message"] == "Списки и маршруты сохранены"
    assert prompts.select_calls[-1]["choice_titles"] == [
        "🚀 Запустить обновление на этом маршрутизаторе",
        "↩ Главное меню",
    ]
    assert len(sync_orchestrator.calls) == 1
    sync_config, trigger = sync_orchestrator.calls[0]
    assert trigger is RunTrigger.MANUAL
    assert [router.id for router in sync_config.routers] == ["router-1"]
    assert {mapping.router_id for mapping in sync_config.mappings} == {"router-1"}
    assert all(Path(path).is_absolute() for path in (sync_config.runtime.artifacts_dir,))

    output = console.export_text()
    assert "Sync: run_id=run-sync status=success artifact=data/artifacts/run-sync.json" in output
    assert "router-1" in output
    assert "изменено=1 ошибок=0" in output


def test_edit_router_switches_password_file_to_env_and_clears_password_file(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["router-2"],
        text_answers=[
            "Router 2",
            "https://router-2.example/rci/",
            "api-user",
        ],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-2",
                "name": "Router 2",
                "rci_url": "https://router-2.example/rci/",
                "username": "api-user",
                "password_file": "/run/secrets/router-2",
                "enabled": True,
            }
        ],
        mappings=[
            {
                "router_id": "router-2",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            }
        ],
    )
    generated_password = "Cc3#defghijklmnopqrs"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._edit_router()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"] == [
        {
            "allowed_source_ips": [],
            "auth_method": "digest",
            "enabled": True,
            "id": "router-2",
            "name": "Router 2",
            "password_env": "FQDN_UPDATER_ROUTER_ROUTER_2_PASSWORD",
            "password_file": None,
            "rci_url": "https://router-2.example/rci/",
            "tags": [],
            "timeout_seconds": 30,
            "username": "api-user",
        }
    ]
    assert SecretEnvFile(path=tmp_path / ".env.secrets").read() == {
        "FQDN_UPDATER_ROUTER_ROUTER_2_PASSWORD": generated_password,
    }


def test_add_router_auto_generates_unique_id_when_secret_env_would_collide(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Home Router",
            "api-user",
            "https://home-router.example/rci/",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    original_password_env = password_env_key_for_router_id("home-router")
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "home_router",
                "name": "Home Router Legacy",
                "rci_url": "https://home-router.example/rci/",
                "username": "api-user",
                "password_env": original_password_env,
                "enabled": True,
            }
        ],
    )
    SecretEnvFile(path=tmp_path / ".env.secrets").write_value(
        key=original_password_env,
        value="old-secret",
    )
    monkeypatch.setattr(panel_module.RciPasswordGenerator, "generate", lambda self: "unused")

    controller._add_router()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"][-1]["id"] == "home-router-2"
    assert payload["routers"][-1]["password_env"] == "FQDN_UPDATER_ROUTER_HOME_ROUTER_2_PASSWORD"
    assert SecretEnvFile(path=tmp_path / ".env.secrets").read() == {
        "FQDN_UPDATER_ROUTER_HOME_ROUTER_2_PASSWORD": "unused",
        original_password_env: "old-secret",
    }


def test_edit_router_rejects_deterministic_password_env_collisions_before_secret_write(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["home-router"],
        text_answers=[
            "Home Router",
            "https://home-router.example/rci/",
            "api-user",
        ],
        confirm_answers=[True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    password_env = password_env_key_for_router_id("home-router")
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "home-router",
                "name": "Home Router",
                "rci_url": "https://home-router.example/rci/",
                "username": "api-user",
                "password_file": "/run/secrets/home-router",
                "enabled": True,
            },
            {
                "id": "home_router",
                "name": "Home Router Alias",
                "rci_url": "https://home-router-alias.example/rci/",
                "username": "api-user",
                "password_env": password_env,
                "enabled": True,
            },
        ],
    )
    SecretEnvFile(path=tmp_path / ".env.secrets").write_value(key=password_env, value="old-secret")
    original_config = controller._config_path.read_text(encoding="utf-8")
    monkeypatch.setattr(panel_module.RciPasswordGenerator, "generate", lambda self: "unused")

    def _unexpected_write_value(*args, **kwargs) -> None:
        raise AssertionError("SecretEnvFile.write_value should not be reached on collision")

    monkeypatch.setattr(SecretEnvFile, "write_value", _unexpected_write_value)

    with pytest.raises(RuntimeError) as exc_info:
        controller._edit_router()

    assert f"Password env '{password_env}' уже используется роутером 'home_router'" in str(
        exc_info.value
    )
    assert controller._config_path.read_text(encoding="utf-8") == original_config
    assert SecretEnvFile(path=tmp_path / ".env.secrets").read() == {password_env: "old-secret"}


def test_add_router_rolls_back_config_when_secret_write_fails(tmp_path, monkeypatch) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Home Router",
            "api-user",
            "https://home-router.example/rci/",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    original_config = controller._config_path.read_text(encoding="utf-8")
    secret_path = tmp_path / ".env.secrets"
    secret_path.write_text("UNRELATED_SECRET=old-secret\n", encoding="utf-8")
    generated_password = "Ff6*ghijklmnopqrstuv"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    def _failing_write_value(*args, **kwargs) -> None:
        raise RuntimeError("secret write failed")

    monkeypatch.setattr(SecretEnvFile, "write_value", _failing_write_value)

    with pytest.raises(RuntimeError) as exc_info:
        controller._add_router()

    assert "secret write failed" in str(exc_info.value)
    assert controller._config_path.read_text(encoding="utf-8") == original_config
    assert SecretEnvFile(path=secret_path).read() == {"UNRELATED_SECRET": "old-secret"}


def test_edit_router_rolls_back_password_file_reference_when_secret_write_fails(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["home-router"],
        text_answers=[
            "Home Router",
            "https://home-router.example/rci/",
            "api-user",
        ],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "home-router",
                "name": "Home Router",
                "rci_url": "https://home-router.example/rci/",
                "username": "api-user",
                "password_file": "/run/secrets/home-router",
                "enabled": True,
            }
        ],
    )
    original_config = controller._config_path.read_text(encoding="utf-8")
    secret_path = tmp_path / ".env.secrets"
    secret_path.write_text("UNRELATED_SECRET=old-secret\n", encoding="utf-8")
    generated_password = "Gg7(ghijklmnopqrstuvw"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    def _failing_write_value(*args, **kwargs) -> None:
        raise RuntimeError("secret write failed")

    monkeypatch.setattr(SecretEnvFile, "write_value", _failing_write_value)

    with pytest.raises(RuntimeError) as exc_info:
        controller._edit_router()

    assert "secret write failed" in str(exc_info.value)
    assert controller._config_path.read_text(encoding="utf-8") == original_config
    assert "/run/secrets/home-router" in controller._config_path.read_text(encoding="utf-8")
    assert SecretEnvFile(path=secret_path).read() == {"UNRELATED_SECRET": "old-secret"}
