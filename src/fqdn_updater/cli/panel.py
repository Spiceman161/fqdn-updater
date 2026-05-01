from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fqdn_updater.application.dry_run_orchestration import (
    DryRunExecutionResult,
    DryRunOrchestrator,
)
from fqdn_updater.application.password_generation import (
    RciPasswordGenerator as RciPasswordGenerator,
)
from fqdn_updater.application.run_history import RecentRun, RunHistoryResult
from fqdn_updater.application.service_sync_planning import ServiceSyncPlanner
from fqdn_updater.application.sync_orchestration import SyncExecutionResult, SyncOrchestrator
from fqdn_updater.cli import panel_formatting as panel_formatting
from fqdn_updater.cli import panel_schedule as panel_schedule
from fqdn_updater.cli.panel_dependencies import PanelDependencies, build_panel_dependencies
from fqdn_updater.cli.panel_prompts import (
    CheckboxTableMeta,
    PromptAdapter,
    PromptChoice,
    QuestionaryPromptAdapter,
)
from fqdn_updater.domain.config_schema import (
    AppConfig,
    RouterConfig,
    RouterServiceMappingConfig,
)
from fqdn_updater.domain.keenetic import RouteTargetCandidate
from fqdn_updater.domain.run_artifact import (
    RouterResultStatus,
    RunTrigger,
)
from fqdn_updater.domain.schedule import RuntimeScheduleConfig
from fqdn_updater.domain.status_diagnostics import StatusDiagnosticsResult
from fqdn_updater.infrastructure.run_lock import FileRunLockManager
from fqdn_updater.infrastructure.run_logging import RunLoggerFactory
from fqdn_updater.infrastructure.secret_env_file import (
    SecretEnvFile,
    password_env_key_for_router_id,
)
from fqdn_updater.infrastructure.service_count_cache import (
    CachingSourceLoadingService,
    resolve_service_count_cache_path,
)

SCHEDULE_MENU_HINT_LINES = panel_schedule.SCHEDULE_MENU_HINT_LINES
_schedule_summary_table = panel_schedule._schedule_summary_table
ROOT_PANEL_WIDTH = panel_formatting.ROOT_PANEL_WIDTH
DISCOVERY_ERROR_MESSAGE_LIMIT = panel_formatting.DISCOVERY_ERROR_MESSAGE_LIMIT
SERVICE_SELECTION_SERVICE_WIDTH = panel_formatting.SERVICE_SELECTION_SERVICE_WIDTH
SERVICE_SELECTION_COUNT_WIDTH = panel_formatting.SERVICE_SELECTION_COUNT_WIDTH
KEENETIC_DOMAIN_SELECTION_LIMIT = panel_formatting.KEENETIC_DOMAIN_SELECTION_LIMIT
SERVICE_SELECTION_GROUPS = panel_formatting.SERVICE_SELECTION_GROUPS
SERVICE_DISPLAY_LABELS = panel_formatting.SERVICE_DISPLAY_LABELS
ServiceEntryCounts = panel_formatting.ServiceEntryCounts
_effective_service_selection = panel_formatting._effective_service_selection
_enabled_service_selection_groups = panel_formatting._enabled_service_selection_groups
_find_router = panel_formatting._find_router
_format_connected = panel_formatting._format_connected
_format_dashboard_last_run_at = panel_formatting._format_dashboard_last_run_at
_format_dashboard_router_run_status = panel_formatting._format_dashboard_router_run_status
_format_entry_count = panel_formatting._format_entry_count
_format_service_list = panel_formatting._format_service_list
_format_validation_error = panel_formatting._format_validation_error
_manual_run_selection_summary = panel_formatting._manual_run_selection_summary
_route_candidate_title = panel_formatting._route_candidate_title
_router_selection_column_widths = panel_formatting._router_selection_column_widths
_router_selection_header = panel_formatting._router_selection_header
_router_selection_title = panel_formatting._router_selection_title
_router_state_label = panel_formatting._router_state_label
_router_toggle_header = panel_formatting._router_toggle_header
_router_toggle_summary = panel_formatting._router_toggle_summary
_router_toggle_title = panel_formatting._router_toggle_title
_service_display_label = panel_formatting._service_display_label
_service_entry_counts_from_report = panel_formatting._service_entry_counts_from_report
_service_entry_counts_from_snapshot = panel_formatting._service_entry_counts_from_snapshot
_service_selection_header = panel_formatting._service_selection_header
_service_selection_title = panel_formatting._service_selection_title
_service_selection_totals_line = panel_formatting._service_selection_totals_line
_shell_quote_path = panel_formatting._shell_quote_path
_truncate_discovery_error_message = panel_formatting._truncate_discovery_error_message

DEFAULT_SELECTED_SERVICES = frozenset(
    {
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
)
DEFAULT_INTERFACE_NAME = "Wireguard0"
DEFAULT_RCI_TIMEOUT_SECONDS = 30
CONTAINER_WORKDIR = Path("/work")
MAIN_MENU_HINT_LINES = (
    "Для начала работы добавьте маршрутизатор Keenetic с ОС версии 5 и выше.",
    "Затем настройте обновление списков по расписанию.",
)
MANUAL_RUN_HINT_LINES = (
    "Dry-run проверит план без записи в Keenetic; sync сразу применит сохранённые mappings.",
    "Для dry-run и sync выберите один или несколько маршрутизаторов для запуска.",
    "При sync будут применены только сохранённые managed mappings; перед записью панель прочитает "
    "текущее состояние Keenetic.",
)
ROUTER_MENU_HINT_LINES = (
    "Здесь можно добавить новый маршрутизатор, изменить его параметры, проверить связь "
    "или сменить статус.",
    "Списки и маршруты не меняются до подтверждения сохранения.",
)
SERVICE_SELECTION_HINT_LINES = (
    "Для каждого выбранного сервиса будет создан свой список в разделе «Маршрутизация» Keenetic.",
    "Лимит доменов роутеров Keenetic составляет 1024 записи. "
    "Вам необходимо выбрать не более этого количества записей.",
    "Для IPv4+IPv6 действует отдельный лимит: около 4000 subnet-записей суммарно на роутер.",
)
ADD_ROUTER_HINT_LINES = ("Введите имя нового маршрутизатора.",)
ADD_ROUTER_RCI_URL_HINT_LINES = (
    "Нажмите кнопку копирования в новой строке «Доступ к веб-приложениям домашней сети».",
    "Скопированный URL должен начинаться с http://rci.",
)
ADD_ROUTER_USERNAME_HINT_LINES = (
    "Где взять RCI username: на Keenetic откройте раздел «Доменное имя».",
    "Проверьте, что доменное имя уже создано и включён доступ из Интернета.",
    "Создайте новый «Доступ к веб-приложениям домашней сети» с именем rci.",
    "Выберите «Авторизованный доступ», «Это устройство Keenetic», протокол HTTP и TCP порт 79.",
    "Добавьте нового пользователя и введите имя этого пользователя в поле ниже.",
)
ADD_ROUTER_PASSWORD_HINT_LINES = (
    "Сейчас задайте этот стойкий пароль новому Keenetic-пользователю, которого привязали к rci.",
    "Скопировать пароль можно через Ctrl+Shift+C.",
    "Поставьте галочку в столбце «Доступ» напротив нового пользователя и сохраните подключение.",
    "После этого вернитесь в мастер и продолжайте настройку KeenDNS RCI URL.",
)
EDIT_ROUTER_PASSWORD_HINT_LINES = (
    "Сейчас обновите пароль у low-privilege RCI пользователя на Keenetic.",
    "Скопировать пароль можно через Ctrl+Shift+C.",
    "После обновления пароля на Keenetic вернитесь в мастер и подтвердите шаг.",
)
ADD_ROUTER_SAVE_HINT_LINES = (
    "Проверьте введенные данные и подтвердите сохранение маршрутизатора.",
)
BASE_ROUTE_INTERFACE_HINT_LINES = (
    "Укажите маршрут, который будет использоваться по умолчанию для выбранных списков.",
)
GOOGLE_AI_OVERRIDE_HINT_LINES = (
    "Для корректной работы AI сервисов от Google можно указать другой отдельный интерфейс.",
)


@dataclass(frozen=True)
class RouteTargetDraft:
    route_target_type: Literal["interface", "gateway"]
    route_target_value: str
    route_interface: str | None = None

    def summary(self) -> str:
        if self.route_target_type == "interface":
            return f"interface:{self.route_target_value}"
        if self.route_interface:
            return f"gateway:{self.route_target_value} via {self.route_interface}"
        return f"gateway:{self.route_target_value}"


@dataclass(frozen=True)
class MappingPlan:
    default_target: RouteTargetDraft
    google_ai_target: RouteTargetDraft | None = None

    def target_for_service(self, service_key: str) -> RouteTargetDraft:
        if service_key == "google_ai" and self.google_ai_target is not None:
            return self.google_ai_target
        return self.default_target


@dataclass(frozen=True)
class RouterLastRun:
    finished_at: datetime
    status: RouterResultStatus


class PanelController:
    """Interactive terminal control panel for local config management."""

    def __init__(
        self,
        *,
        config_path: Path,
        console: Console | None = None,
        prompts: PromptAdapter | None = None,
        dependencies: PanelDependencies | None = None,
    ) -> None:
        from fqdn_updater.cli.panel_router_flow import PanelRouterFlow
        from fqdn_updater.cli.panel_runs import PanelRunsFlow
        from fqdn_updater.cli.panel_schedule import PanelScheduleFlow

        panel_dependencies = dependencies or build_panel_dependencies()
        self._config_path = config_path
        self._console = console or Console()
        self._prompts = prompts or QuestionaryPromptAdapter(console=self._console)
        self._repository = panel_dependencies.repository
        self._bootstrap_service = panel_dependencies.bootstrap_service
        self._management_service = panel_dependencies.management_service
        self._secret_resolver = panel_dependencies.secret_resolver
        self._client_factory = panel_dependencies.client_factory
        self._artifact_repository = panel_dependencies.artifact_repository
        self._route_target_discovery_service = panel_dependencies.route_target_discovery_service
        self._run_history_service = panel_dependencies.run_history_service
        self._status_service = panel_dependencies.status_service
        self._schedule_installer = panel_dependencies.schedule_installer
        self._source_loading_service = panel_dependencies.source_loading_service
        self._service_count_cache_repository = panel_dependencies.service_count_cache_repository
        self._dry_run_orchestrator = panel_dependencies.dry_run_orchestrator
        self._sync_orchestrator = panel_dependencies.sync_orchestrator
        self._password_generator = panel_dependencies.password_generator
        self._router_flow = PanelRouterFlow(panel=self)
        self._runs_flow = PanelRunsFlow(panel=self)
        self._schedule_flow = PanelScheduleFlow(panel=self)

    def run(self) -> None:
        self._ensure_config_exists()
        self._management_service.sync_builtin_services(path=self._config_path)
        while True:
            config = self._load_config()
            self._render_dashboard(config=config)
            choice = self._prompts.select(
                message="Выберите раздел панели",
                choices=[
                    PromptChoice("Маршрутизаторы", "routers"),
                    PromptChoice("Списки и маршруты", "lists"),
                    PromptChoice("Ручной запуск", "manual-run"),
                    PromptChoice("Расписание", "schedule"),
                    PromptChoice("Журнал", "runs"),
                    PromptChoice("Проверка конфига", "config"),
                    PromptChoice("О панели", "about"),
                    PromptChoice("Выход", "exit"),
                ],
                default="routers",
                instruction="Стрелки выбирают, Enter открывает, Esc выходит.",
                hint_lines=MAIN_MENU_HINT_LINES,
            )
            if choice in {None, "exit"}:
                return
            if choice == "routers":
                self._router_menu()
            elif choice == "lists":
                self._lists_menu()
            elif choice == "manual-run":
                self._manual_run_menu()
            elif choice == "schedule":
                self._schedule_menu()
            elif choice == "runs":
                self._runs_menu()
            elif choice == "config":
                self._config_menu(config=config)
            elif choice == "about":
                self._about_menu()

    def _ensure_config_exists(self) -> None:
        if self._config_path.exists():
            return

        should_create = self._prompts.confirm(
            message=f"Файл конфига '{self._config_path}' не найден. Создать scaffold?",
            default=True,
        )
        if not should_create:
            raise RuntimeError(f"Config file не найден: {self._config_path}")
        self._bootstrap_service.create_default_config(path=self._config_path)
        self._console.print(f"[green]Стартовый config создан:[/green] {self._config_path}")

    def _load_config(self) -> AppConfig:
        return self._repository.load(path=self._config_path)

    def _render_dashboard(self, *, config: AppConfig) -> None:
        self._console.clear()
        title = Text("FQDN-updater", style="bold white")
        subtitle = Text("операторская панель", style="bold cyan")
        header = Text.assemble(title, "  ", subtitle)
        self._console.print(Panel(header, border_style="bright_cyan"))

        router_table = Table(show_header=True, header_style="bold white", box=None)
        router_table.add_column("Маршрутизатор", no_wrap=True)
        router_table.add_column("Состояние", no_wrap=True)
        router_table.add_column("RCI")
        router_table.add_column("Последний запуск", no_wrap=True)
        router_table.add_column("Статус", no_wrap=True)
        last_runs = self._last_router_runs(config=config)
        for router in config.routers:
            last_run = last_runs.get(router.id)
            router_table.add_row(
                router.id,
                _router_state_label(router.enabled),
                str(router.rci_url),
                _format_dashboard_last_run_at(last_run.finished_at) if last_run else "[dim]-[/dim]",
                (
                    _format_dashboard_router_run_status(last_run.status)
                    if last_run
                    else "[dim]-[/dim]"
                ),
            )
        if not config.routers:
            router_table.add_row("[dim]нет[/dim]", "-", "-", "-", "-")
        self._console.print(
            Panel(
                router_table,
                title="Маршрутизаторы",
                border_style="bright_black",
            )
        )
        self._console.print(
            Panel(
                _schedule_summary_table(config.runtime.schedule),
                title="Расписание",
                border_style="bright_black",
            )
        )

    def _last_router_runs(self, *, config: AppConfig) -> dict[str, RouterLastRun]:
        history = self._run_history_service.list_recent(
            config=config,
            config_path=self._config_path,
            limit=50,
        )
        last_runs: dict[str, RouterLastRun] = {}
        for run in history.runs:
            artifact = run.artifact
            for router_result in artifact.router_results:
                existing = last_runs.get(router_result.router_id)
                if existing is None or artifact.finished_at > existing.finished_at:
                    last_runs[router_result.router_id] = RouterLastRun(
                        finished_at=artifact.finished_at,
                        status=router_result.status,
                    )
        return last_runs

    def _router_menu(self) -> None:
        while True:
            config = self._load_config()
            has_routers = bool(config.routers)
            choice = self._prompts.select(
                message="Маршрутизаторы",
                choices=[
                    PromptChoice("Добавить новый маршрутизатор", "add"),
                    PromptChoice(
                        "Изменить параметры маршрутизатора",
                        "edit",
                        disabled=None if has_routers else "Нет настроенных маршрутизаторов",
                    ),
                    PromptChoice(
                        "Включить или выключить маршрутизатор",
                        "toggle",
                        disabled=None if has_routers else "Нет настроенных маршрутизаторов",
                    ),
                    PromptChoice(
                        "Проверка связи с маршрутизаторами",
                        "status",
                        disabled=None if has_routers else "Нет настроенных маршрутизаторов",
                    ),
                    PromptChoice("Главное меню", "back"),
                ],
                default="add",
                hint_lines=ROUTER_MENU_HINT_LINES,
            )
            if choice in {None, "back"}:
                return
            if choice == "add":
                if self._add_router():
                    return
            elif choice == "edit":
                self._edit_router()
            elif choice == "toggle":
                self._toggle_router_enabled()
            elif choice == "status":
                self._run_status_diagnostics()

    def _add_router(self) -> bool:
        return self._router_flow.add_router()

    def _edit_router(self) -> None:
        self._router_flow.edit_router()

    def _toggle_router_enabled(self) -> None:
        self._router_flow.toggle_router_enabled()

    def _lists_menu(self) -> None:
        config = self._load_config()
        router = self._select_router(
            config=config,
            message="Выберите маршрутизатор для списков и маршрутов",
            back_title="Главное меню",
        )
        if router is None:
            return

        editable_mappings, preserved_mappings = _partition_router_mappings(
            config=config,
            router_id=router.id,
        )
        previously_selected = {mapping.service_key for mapping in editable_mappings}
        selected_services = self._prompt_service_selection(
            config=config,
            selected=previously_selected,
        )
        if selected_services is None:
            return

        mapping_plan = None
        if selected_services:
            mapping_plan = self._prompt_mapping_plan(
                config=config,
                router=router,
                editable_mappings=editable_mappings,
                selected_services=selected_services,
                missing_secret_message=None,
            )
            if mapping_plan is None:
                return

        editable_mapping_payloads = self._build_router_mappings(
            router_id=router.id,
            selected_services=selected_services,
            existing_mappings={mapping.service_key: mapping for mapping in editable_mappings},
            mapping_plan=mapping_plan,
        )
        full_mapping_payloads = [
            *(mapping.model_dump(mode="json") for mapping in preserved_mappings),
            *editable_mapping_payloads,
        ]

        added_services = sorted(selected_services - previously_selected)
        removed_services = sorted(previously_selected - selected_services)
        kept_services = sorted(previously_selected & selected_services)
        self._render_summary(
            title="Проверка сохранения",
            rows=[
                ("Операция", "обновить списки и маршруты"),
                ("Маршрутизатор", router.id),
                ("Добавить", _format_service_list(added_services) or "нет"),
                ("Удалить", _format_service_list(removed_services) or "нет"),
                ("Оставить", _format_service_list(kept_services) or "нет"),
                (
                    "Сервисы после сохранения",
                    _format_service_list(sorted(selected_services)) or "нет",
                ),
                (
                    "Базовый target",
                    mapping_plan.default_target.summary() if mapping_plan is not None else "нет",
                ),
                (
                    "google_ai override",
                    (
                        mapping_plan.google_ai_target.summary()
                        if mapping_plan is not None and mapping_plan.google_ai_target is not None
                        else "нет"
                    ),
                ),
                ("Сохранённые mappings", str(len(preserved_mappings))),
            ],
        )
        should_save = self._prompts.confirm(
            message="Сохранить списки и маршруты для маршрутизатора?",
            default=True,
        )
        if not should_save:
            return

        self._management_service.replace_router_mappings(
            path=self._config_path,
            router_id=router.id,
            mappings=full_mapping_payloads,
        )
        self._console.print("[green]Списки и маршруты сохранены.[/green]")
        choice = self._prompts.select(
            message="Списки и маршруты сохранены",
            choices=[
                PromptChoice("Запустить обновление на этом маршрутизаторе", "sync-router"),
                PromptChoice("Главное меню", "back"),
            ],
            default="sync-router",
            hint_lines=(
                "Команда применит managed mappings только выбранного маршрутизатора.",
                "Перед записью будет прочитано текущее состояние Keenetic.",
            ),
        )
        if choice == "sync-router":
            self._run_sync_for_router(router_id=router.id)

    def _runs_menu(self) -> None:
        self._runs_flow.runs_menu()

    def _manual_run_menu(self) -> None:
        config = self._load_config()
        if not config.routers:
            self._console.print("[yellow]Нет настроенных маршрутизаторов.[/yellow]")
            self._pause()
            return

        action = self._prompts.select(
            message="Ручной запуск",
            choices=[
                PromptChoice("Dry-run (тестовый запуск без изменения списков)", "dry-run"),
                PromptChoice("Sync (применить изменения в Keenetic)", "sync"),
                PromptChoice("Главное меню", "back"),
            ],
            default="dry-run",
            hint_lines=MANUAL_RUN_HINT_LINES,
        )
        if action in {None, "back"}:
            return

        selected_router_ids = self._select_manual_run_router_ids(config=config)
        if selected_router_ids is None:
            return
        if not selected_router_ids:
            self._console.print("[yellow]Маршрутизаторы для запуска не выбраны.[/yellow]")
            self._pause()
            return

        if action == "dry-run":
            self._run_dry_run_preview_for_routers(router_ids=tuple(selected_router_ids))
            return

        self._run_sync_for_routers(router_ids=tuple(selected_router_ids))

    def _select_manual_run_router_ids(self, *, config: AppConfig) -> list[str] | None:
        router_id_width, router_name_width = _router_selection_column_widths(config.routers)
        return self._prompts.checkbox(
            message="Ручной запуск",
            choices=[
                PromptChoice(
                    title=_router_selection_title(
                        router=router,
                        router_id_width=router_id_width,
                        router_name_width=router_name_width,
                    ),
                    value=router.id,
                    checked=router.enabled,
                    answer_title=router.id,
                )
                for router in config.routers
            ],
            instruction=(
                "Стрелки выбирают, Пробел отмечает, Enter запускает, Esc возвращает назад."
            ),
            hint_lines=MANUAL_RUN_HINT_LINES,
            table_meta=CheckboxTableMeta(
                header=_router_selection_header(
                    router_id_width=router_id_width,
                    router_name_width=router_name_width,
                ),
                summary=lambda selected_values: _manual_run_selection_summary(
                    selected_values=selected_values,
                ),
            ),
        )

    def _schedule_menu(self) -> None:
        self._schedule_flow.schedule_menu()

    def _edit_schedule(self) -> None:
        self._schedule_flow.edit_schedule()

    def _disable_schedule(self) -> None:
        self._schedule_flow.disable_schedule()

    def _install_schedule(self) -> None:
        self._schedule_flow.install_schedule()

    def _config_menu(self, *, config: AppConfig) -> None:
        self._console.print("[green]Конфиг валиден.[/green]")
        self._console.print(
            f"маршрутизаторы={len(config.routers)} services={len(config.services)} "
            f"mappings={len(config.mappings)}"
        )
        self._console.print(f"secrets_env_file={config.runtime.secrets_env_file}")
        self._pause()

    def _about_menu(self) -> None:
        self._console.print("[bold]FQDN-updater panel[/bold]")
        self._console.print(
            "Панель предназначена для обновления списков маршрутизации маршрутизаторов "
            "Keenetic (Netcraze) на основе репозитория "
            "https://github.com/itdoginfo/allow-domains."
        )
        self._console.print(
            "FQDN-updater поддерживает эти списки в актуальном состоянии: загружает "
            "домены и подсети, обновляет managed FQDN-группы и применяет сохранённые "
            "маршруты на выбранных маршрутизаторах."
        )
        self._console.print(
            "Панель помогает безопасно настроить маршрутизаторы, списки, маршруты, "
            "расписание и ручной запуск без прямого редактирования config."
        )
        self._pause()

    def _select_router(
        self,
        *,
        config: AppConfig,
        message: str,
        back_title: str = "Назад",
    ) -> RouterConfig | None:
        return self._router_flow.select_router(
            config=config,
            message=message,
            back_title=back_title,
        )

    def _prompt_service_selection(
        self,
        *,
        config: AppConfig,
        selected: set[str],
        hint_lines: tuple[str, ...] | None = None,
    ) -> set[str] | None:
        return self._router_flow.prompt_service_selection(
            config=config,
            selected=selected,
            hint_lines=hint_lines,
        )

    def _load_service_entry_counts(self, *, config: AppConfig) -> dict[str, ServiceEntryCounts]:
        return self._router_flow.load_service_entry_counts(config=config)

    def _prompt_mapping_plan(
        self,
        *,
        config: AppConfig,
        router: RouterConfig,
        editable_mappings: list[RouterServiceMappingConfig],
        selected_services: set[str],
        missing_secret_message: str | None = None,
        discovery_password: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> MappingPlan | None:
        return self._router_flow.prompt_mapping_plan(
            config=config,
            router=router,
            editable_mappings=editable_mappings,
            selected_services=selected_services,
            missing_secret_message=missing_secret_message,
            discovery_password=discovery_password,
            hint_lines=hint_lines,
        )

    def _prompt_route_target(
        self,
        *,
        config: AppConfig,
        router: RouterConfig,
        label: str,
        default_target: RouteTargetDraft,
        missing_secret_message: str | None,
        discovery_password: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> RouteTargetDraft | None:
        return self._router_flow.prompt_route_target(
            config=config,
            router=router,
            label=label,
            default_target=default_target,
            missing_secret_message=missing_secret_message,
            discovery_password=discovery_password,
            hint_lines=hint_lines,
        )

    def _prompt_interface_target(
        self,
        *,
        config: AppConfig,
        router: RouterConfig,
        label: str,
        default_value: str,
        missing_secret_message: str | None,
        discovery_password: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> RouteTargetDraft | None:
        return self._router_flow.prompt_interface_target(
            config=config,
            router=router,
            label=label,
            default_value=default_value,
            missing_secret_message=missing_secret_message,
            discovery_password=discovery_password,
            hint_lines=hint_lines,
        )

    def _prompt_gateway_target(
        self,
        *,
        label: str,
        default_target: RouteTargetDraft,
        hint_lines: tuple[str, ...] | None = None,
    ) -> RouteTargetDraft | None:
        return self._router_flow.prompt_gateway_target(
            label=label,
            default_target=default_target,
            hint_lines=hint_lines,
        )

    def _build_router_mappings(
        self,
        *,
        router_id: str,
        selected_services: set[str],
        existing_mappings: dict[str, RouterServiceMappingConfig],
        mapping_plan: MappingPlan | None,
    ) -> list[dict[str, Any]]:
        return self._router_flow.build_router_mappings(
            router_id=router_id,
            selected_services=selected_services,
            existing_mappings=existing_mappings,
            mapping_plan=mapping_plan,
        )

    def _discover_route_targets(
        self,
        *,
        config: AppConfig,
        router: RouterConfig | None,
        missing_secret_message: str | None = None,
        discovery_password: str | None = None,
    ) -> tuple[RouteTargetCandidate, ...]:
        return self._router_flow.discover_route_targets(
            config=config,
            router=router,
            missing_secret_message=missing_secret_message,
            discovery_password=discovery_password,
        )

    def _probe_router_connectivity(
        self,
        *,
        config: AppConfig,
        router: RouterConfig | None,
        password_override: str | None = None,
    ) -> str | None:
        return self._router_flow.probe_router_connectivity(
            config=config,
            router=router,
            password_override=password_override,
        )

    def _render_runs_screen(
        self,
        *,
        config: AppConfig,
        history: RunHistoryResult,
        page_index: int,
        page_size: int,
    ) -> None:
        self._runs_flow.render_runs_screen(
            config=config,
            history=history,
            page_index=page_index,
            page_size=page_size,
        )

    def _render_run_details_screen(self, *, config: AppConfig, run: RecentRun) -> None:
        self._runs_flow.render_run_details_screen(config=config, run=run)

    def _render_schedule_screen(self, *, schedule: RuntimeScheduleConfig) -> None:
        self._schedule_flow.render_schedule_screen(schedule=schedule)

    def _run_status_diagnostics(self) -> None:
        config = self._config_with_resolved_runtime_paths(config=self._load_config())
        try:
            self._load_runtime_secret_env_file(config=config)
            result = self._status_service.check(config=config)
        except RuntimeError as exc:
            self._console.print(f"[red]Status diagnostics не прошёл:[/red] {exc}")
            self._pause()
            return

        self._render_status_result(result=result)
        self._pause()

    def _run_dry_run_preview(self) -> None:
        config = self._config_with_resolved_runtime_paths(config=self._load_config())
        self._run_dry_run_preview_with_config(config=config)

    def _run_dry_run_preview_for_routers(self, *, router_ids: tuple[str, ...]) -> None:
        config = self._config_with_resolved_runtime_paths(config=self._load_config())
        router_config = _config_for_routers(config=config, router_ids=router_ids)
        self._run_dry_run_preview_with_config(config=router_config)

    def _run_dry_run_preview_with_config(self, *, config: AppConfig) -> None:
        try:
            self._load_runtime_secret_env_file(config=config)
            orchestrator = self._dry_run_orchestrator
            if isinstance(orchestrator, DryRunOrchestrator):
                orchestrator = self._build_dry_run_orchestrator(config=config)
            result = orchestrator.run(
                config=config,
                trigger=RunTrigger.MANUAL,
            )
        except RuntimeError as exc:
            self._console.print(f"[red]Dry-run preview не прошёл:[/red] {exc}")
            self._pause()
            return

        self._render_dry_run_result(result=result)
        self._pause()

    def _run_sync_for_router(self, *, router_id: str) -> None:
        config = self._config_with_resolved_runtime_paths(config=self._load_config())
        router_config = _config_for_router(config=config, router_id=router_id)
        self._run_sync_with_config(config=router_config)

    def _run_sync_for_routers(self, *, router_ids: tuple[str, ...]) -> None:
        config = self._config_with_resolved_runtime_paths(config=self._load_config())
        router_config = _config_for_routers(config=config, router_ids=router_ids)
        self._run_sync_with_config(config=router_config)

    def _run_sync_with_config(self, *, config: AppConfig) -> None:
        try:
            self._load_runtime_secret_env_file(config=config)
            orchestrator = self._sync_orchestrator
            if isinstance(orchestrator, SyncOrchestrator):
                orchestrator = self._build_sync_orchestrator(config=config)
            result = orchestrator.run(
                config=config,
                trigger=RunTrigger.MANUAL,
            )
        except RuntimeError as exc:
            self._console.print(f"[red]Sync не прошёл:[/red] {exc}")
            self._pause()
            return

        self._render_sync_result(result=result)
        self._pause()

    def _render_status_result(self, *, result: StatusDiagnosticsResult) -> None:
        self._runs_flow.render_status_result(result=result)

    def _render_dry_run_result(self, *, result: DryRunExecutionResult) -> None:
        self._runs_flow.render_dry_run_result(result=result)

    def _render_sync_result(self, *, result: SyncExecutionResult) -> None:
        self._runs_flow.render_sync_result(result=result)

    def _render_route_target_candidates(
        self,
        *,
        candidates: tuple[RouteTargetCandidate, ...],
    ) -> None:
        table = Table(show_header=True, header_style="bold white")
        table.add_column("Интерфейс")
        table.add_column("Подключён")
        table.add_column("Status")
        table.add_column("Деталь")
        for candidate in candidates:
            table.add_row(
                candidate.display_name or candidate.value,
                _format_connected(candidate.connected),
                candidate.status or "[dim]-[/dim]",
                candidate.detail or "[dim]-[/dim]",
            )
        self._console.print(Panel(table, title="WireGuard discovery", border_style="cyan"))

    def _render_summary(self, *, title: str, rows: list[tuple[str, str]]) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold white")
        table.add_column(style="bright_cyan")
        for label, value in rows:
            table.add_row(label, value)
        self._console.print(Panel(table, title=title, border_style="bright_cyan"))

    def _show_generated_password(self, *, password: str) -> None:
        self._console.print("[bold]Новый пароль показан один раз:[/bold]")
        self._console.print(password)
        self._console.print(
            "Сейчас обновите пароль у low-privilege RCI пользователя на Keenetic. "
            "Панель больше его не покажет."
        )

    def _print_discovery_error(self, message: str) -> None:
        self._console.print(
            Text.assemble(
                ("WireGuard discovery не прошёл: ", "yellow"),
                (_truncate_discovery_error_message(message), "red"),
            )
        )

    def _print_router_connectivity_error(self) -> None:
        self._console.print("[red]Проверка связи с маршрутизатором не прошла.[/red]")

    def _pause(self) -> None:
        self._prompts.pause(message="Нажмите любую клавишу для продолжения...")

    def _build_dry_run_orchestrator(self, *, config: AppConfig) -> DryRunOrchestrator:
        return DryRunOrchestrator(
            source_loader=self._service_count_source_loader(config=config),
            secret_resolver=self._secret_resolver,
            client_factory=self._client_factory,
            planner=ServiceSyncPlanner(),
            artifact_writer=self._artifact_repository,
            logger_factory=RunLoggerFactory(),
            run_lock_manager=FileRunLockManager(),
        )

    def _build_sync_orchestrator(self, *, config: AppConfig) -> SyncOrchestrator:
        return SyncOrchestrator(
            source_loader=self._service_count_source_loader(config=config),
            secret_resolver=self._secret_resolver,
            client_factory=self._client_factory,
            planner=ServiceSyncPlanner(),
            artifact_writer=self._artifact_repository,
            logger_factory=RunLoggerFactory(),
            run_lock_manager=FileRunLockManager(),
        )

    def _service_count_source_loader(self, *, config: AppConfig) -> CachingSourceLoadingService:
        return CachingSourceLoadingService(
            source_loader=self._source_loading_service,
            cache_repository=self._service_count_cache_repository,
            cache_path=self._service_count_cache_path(config=config),
        )

    def _service_count_cache_path(self, *, config: AppConfig) -> Path:
        return resolve_service_count_cache_path(
            config_path=self._config_path,
            artifacts_dir=config.runtime.artifacts_dir,
        )

    def _secrets_env_path(self, *, config: AppConfig) -> Path:
        return self._resolve_config_relative_path(config.runtime.secrets_env_file)

    def _load_runtime_secret_env_file(self, *, config: AppConfig) -> None:
        SecretEnvFile(path=self._secrets_env_path(config=config)).load_into_environment()

    def _config_with_resolved_runtime_paths(self, *, config: AppConfig) -> AppConfig:
        runtime = config.runtime.model_copy(
            update={
                "artifacts_dir": str(
                    self._resolve_config_relative_path(config.runtime.artifacts_dir)
                ),
                "logs_dir": str(self._resolve_config_relative_path(config.runtime.logs_dir)),
                "state_dir": str(self._resolve_config_relative_path(config.runtime.state_dir)),
                "secrets_env_file": str(self._secrets_env_path(config=config)),
            }
        )
        return config.model_copy(update={"runtime": runtime})

    def _resolve_config_relative_path(self, configured_path: str) -> Path:
        path = Path(configured_path)
        if path.is_absolute():
            return path
        return self._config_path.parent / path

    def _display_path(self, path: Path | str) -> str:
        candidate = Path(path)
        if not candidate.is_absolute():
            return str(candidate)
        candidate = self._host_accessible_runtime_path(candidate)
        try:
            return str(candidate.relative_to(self._config_path.parent))
        except ValueError:
            return str(candidate)

    def _log_cat_command(self, path: Path | str) -> str:
        candidate = self._host_accessible_runtime_path(path)
        return f"cat {_shell_quote_path(candidate)}"

    def _host_accessible_runtime_path(self, path: Path | str) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._resolve_config_relative_path(str(path))
            return candidate
        try:
            container_relative_path = candidate.relative_to(CONTAINER_WORKDIR)
        except ValueError:
            return candidate
        return self._config_path.parent / container_relative_path


def _config_for_router(*, config: AppConfig, router_id: str) -> AppConfig:
    router = _find_router(config=config, router_id=router_id)
    if router is None:
        raise RuntimeError(f"Router '{router_id}' does not exist")
    return config.model_copy(
        update={
            "routers": [router],
            "mappings": [mapping for mapping in config.mappings if mapping.router_id == router_id],
        }
    )


def _config_for_routers(*, config: AppConfig, router_ids: tuple[str, ...]) -> AppConfig:
    selected_router_ids = set(router_ids)
    routers = [router for router in config.routers if router.id in selected_router_ids]
    found_router_ids = {router.id for router in routers}
    missing_router_ids = sorted(selected_router_ids - found_router_ids)
    if missing_router_ids:
        raise RuntimeError(f"Router '{missing_router_ids[0]}' does not exist")
    return config.model_copy(
        update={
            "routers": routers,
            "mappings": [
                mapping for mapping in config.mappings if mapping.router_id in selected_router_ids
            ],
        }
    )


def _partition_router_mappings(
    *,
    config: AppConfig,
    router_id: str,
) -> tuple[list[RouterServiceMappingConfig], list[RouterServiceMappingConfig]]:
    enabled_services = {service.key for service in config.services if service.enabled}
    editable: list[RouterServiceMappingConfig] = []
    preserved: list[RouterServiceMappingConfig] = []
    for mapping in config.mappings:
        if mapping.router_id != router_id:
            continue
        if mapping.managed and mapping.service_key in enabled_services:
            editable.append(mapping)
        else:
            preserved.append(mapping)
    return editable, preserved


def _derive_mapping_plan_defaults(
    *,
    editable_mappings: list[RouterServiceMappingConfig],
) -> tuple[RouteTargetDraft, bool, RouteTargetDraft | None]:
    default_targets = [
        _mapping_route_target(mapping)
        for mapping in sorted(editable_mappings, key=lambda item: item.service_key)
        if mapping.service_key != "google_ai"
    ]
    unique_default_targets = {
        (
            target.route_target_type,
            target.route_target_value,
            target.route_interface,
        )
        for target in default_targets
    }
    has_inconsistent_default = len(unique_default_targets) > 1

    if default_targets:
        default_target = default_targets[0]
    else:
        google_ai_mapping = next(
            (mapping for mapping in editable_mappings if mapping.service_key == "google_ai"),
            None,
        )
        if google_ai_mapping is None:
            default_target = RouteTargetDraft("interface", DEFAULT_INTERFACE_NAME, None)
        else:
            default_target = _mapping_route_target(google_ai_mapping)

    google_ai_override = None
    google_ai_mapping = next(
        (mapping for mapping in editable_mappings if mapping.service_key == "google_ai"),
        None,
    )
    if google_ai_mapping is not None:
        candidate_target = _mapping_route_target(google_ai_mapping)
        if candidate_target != default_target:
            google_ai_override = candidate_target

    return default_target, has_inconsistent_default, google_ai_override


def _mapping_route_target(mapping: RouterServiceMappingConfig) -> RouteTargetDraft:
    return RouteTargetDraft(
        route_target_type=mapping.route_target_type,
        route_target_value=mapping.route_target_value,
        route_interface=mapping.route_interface,
    )


def _default_interface_target_value(default_target: RouteTargetDraft) -> str:
    if default_target.route_target_type == "interface":
        return default_target.route_target_value
    if default_target.route_interface:
        return default_target.route_interface
    return DEFAULT_INTERFACE_NAME


def _derive_router_id(*, name: str, config: AppConfig) -> str:
    base_slug = _slugify_router_name(name)
    if _router_id_is_available(config=config, router_id=base_slug):
        return base_slug

    suffix = 2
    while True:
        candidate = f"{base_slug}-{suffix}"
        if _router_id_is_available(config=config, router_id=candidate):
            return candidate
        suffix += 1


def _slugify_router_name(name: str) -> str:
    transliterated_name = "".join(
        _CYRILLIC_TO_ASCII.get(character, character) for character in name
    )
    normalized_name = unicodedata.normalize("NFKD", transliterated_name)
    ascii_name = normalized_name.encode("ascii", "ignore").decode("ascii")
    lowered_name = ascii_name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered_name).strip("-")
    if not slug:
        return "router"
    return slug


def _router_id_is_available(*, config: AppConfig, router_id: str) -> bool:
    existing_ids = {router.id for router in config.routers}
    if router_id in existing_ids:
        return False

    candidate_password_env = password_env_key_for_router_id(router_id)
    for router in config.routers:
        existing_password_env = _router_password_env_reference(router)
        if existing_password_env == candidate_password_env:
            return False
    return True


def _ensure_password_env_available(
    *,
    config: AppConfig,
    router_id: str,
    password_env: str,
) -> None:
    for router in config.routers:
        existing_password_env = _router_password_env_reference(router)
        if router.id != router_id and existing_password_env == password_env:
            raise RuntimeError(
                f"Password env '{password_env}' уже используется роутером '{router.id}'"
            )


def _router_password_env_reference(router: RouterConfig) -> str | None:
    if router.password_env is not None:
        return router.password_env
    if router.password_file is not None:
        return password_env_key_for_router_id(router.id)
    return None


_CYRILLIC_TO_ASCII = {
    "А": "A",
    "а": "a",
    "Б": "B",
    "б": "b",
    "В": "V",
    "в": "v",
    "Г": "G",
    "г": "g",
    "Д": "D",
    "д": "d",
    "Е": "E",
    "е": "e",
    "Ё": "E",
    "ё": "e",
    "Ж": "Zh",
    "ж": "zh",
    "З": "Z",
    "з": "z",
    "И": "I",
    "и": "i",
    "Й": "I",
    "й": "i",
    "К": "K",
    "к": "k",
    "Л": "L",
    "л": "l",
    "М": "M",
    "м": "m",
    "Н": "N",
    "н": "n",
    "О": "O",
    "о": "o",
    "П": "P",
    "п": "p",
    "Р": "R",
    "р": "r",
    "С": "S",
    "с": "s",
    "Т": "T",
    "т": "t",
    "У": "U",
    "у": "u",
    "Ф": "F",
    "ф": "f",
    "Х": "Kh",
    "х": "kh",
    "Ц": "Ts",
    "ц": "ts",
    "Ч": "Ch",
    "ч": "ch",
    "Ш": "Sh",
    "ш": "sh",
    "Щ": "Shch",
    "щ": "shch",
    "Ъ": "",
    "ъ": "",
    "Ы": "Y",
    "ы": "y",
    "Ь": "",
    "ь": "",
    "Э": "E",
    "э": "e",
    "Ю": "Yu",
    "ю": "yu",
    "Я": "Ya",
    "я": "ya",
}


def _is_missing_password_env_error(message: str) -> bool:
    return "password env" in message and "is not set" in message
