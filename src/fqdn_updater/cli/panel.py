from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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
from fqdn_updater.cli import panel_formatting, panel_router_support, panel_schedule
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
)
from fqdn_updater.infrastructure.service_count_cache import (
    CachingSourceLoadingService,
    resolve_service_count_cache_path,
)

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
                    _menu_choice(panel_formatting.ICON_ROUTER, "Маршрутизаторы", "routers"),
                    _menu_choice(panel_formatting.ICON_LISTS, "Списки и маршруты", "lists"),
                    _menu_choice(panel_formatting.ICON_RUN, "Ручной запуск", "manual-run"),
                    _menu_choice(panel_formatting.ICON_SCHEDULE, "Расписание", "schedule"),
                    _menu_choice(panel_formatting.ICON_HISTORY, "Журнал", "runs"),
                    _menu_choice(panel_formatting.ICON_CONFIG, "Проверка конфига", "config"),
                    _menu_choice(panel_formatting.ICON_ABOUT, "О панели", "about"),
                    _menu_choice(panel_formatting.ICON_EXIT, "Выход", "exit"),
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
        title = Text(
            panel_formatting._icon_label(panel_formatting.ICON_APP, "FQDN-updater"),
            style="bold white",
        )
        subtitle = Text(
            panel_formatting._icon_label(panel_formatting.ICON_OPERATOR, "операторская панель"),
            style="bold cyan",
        )
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
                panel_formatting._router_state_label(router.enabled),
                str(router.rci_url),
                (
                    panel_formatting._format_dashboard_last_run_at(last_run.finished_at)
                    if last_run
                    else "[dim]-[/dim]"
                ),
                (
                    panel_formatting._format_dashboard_router_run_status(last_run.status)
                    if last_run
                    else "[dim]-[/dim]"
                ),
            )
        if not config.routers:
            router_table.add_row("[dim]нет[/dim]", "-", "-", "-", "-")
        self._console.print(
            Panel(
                router_table,
                title=panel_formatting._icon_label(panel_formatting.ICON_ROUTER, "Маршрутизаторы"),
                border_style="bright_black",
            )
        )
        self._console.print(
            Panel(
                panel_schedule._schedule_summary_table(config.runtime.schedule),
                title=panel_formatting._icon_label(panel_formatting.ICON_SCHEDULE, "Расписание"),
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
                    _menu_choice(
                        panel_formatting.ICON_ADD,
                        "Добавить новый маршрутизатор",
                        "add",
                    ),
                    _menu_choice(
                        panel_formatting.ICON_EDIT,
                        "Изменить параметры маршрутизатора",
                        "edit",
                        disabled=None if has_routers else "Нет настроенных маршрутизаторов",
                    ),
                    _menu_choice(
                        panel_formatting.ICON_TOGGLE,
                        "Включить или выключить маршрутизатор",
                        "toggle",
                        disabled=None if has_routers else "Нет настроенных маршрутизаторов",
                    ),
                    _menu_choice(
                        panel_formatting.ICON_SEARCH,
                        "Проверка связи с маршрутизаторами",
                        "status",
                        disabled=None if has_routers else "Нет настроенных маршрутизаторов",
                    ),
                    _menu_choice(panel_formatting.ICON_BACK, "Главное меню", "back"),
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

        editable_mappings, preserved_mappings = panel_router_support.partition_router_mappings(
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
                ("Добавить", panel_formatting._format_service_list(added_services) or "нет"),
                ("Удалить", panel_formatting._format_service_list(removed_services) or "нет"),
                ("Оставить", panel_formatting._format_service_list(kept_services) or "нет"),
                (
                    "Сервисы после сохранения",
                    panel_formatting._format_service_list(sorted(selected_services)) or "нет",
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
                _menu_choice(
                    panel_formatting.ICON_RUN,
                    "Запустить обновление на этом маршрутизаторе",
                    "sync-router",
                ),
                _menu_choice(panel_formatting.ICON_BACK, "Главное меню", "back"),
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
                _menu_choice(
                    panel_formatting.ICON_DRY_RUN,
                    "Dry-run (тестовый запуск без изменения списков)",
                    "dry-run",
                ),
                _menu_choice(
                    panel_formatting.ICON_RUN,
                    "Sync (применить изменения в Keenetic)",
                    "sync",
                ),
                _menu_choice(panel_formatting.ICON_BACK, "Главное меню", "back"),
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
        router_id_width, router_name_width = panel_formatting._router_selection_column_widths(
            config.routers
        )
        return self._prompts.checkbox(
            message="Ручной запуск",
            choices=[
                PromptChoice(
                    title=panel_formatting._router_selection_title(
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
                header=panel_formatting._router_selection_header(
                    router_id_width=router_id_width,
                    router_name_width=router_name_width,
                ),
                summary=lambda selected_values: panel_formatting._manual_run_selection_summary(
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
        self._console.print(f"[green]{panel_formatting.ICON_CONFIG} Конфиг валиден.[/green]")
        self._console.print(
            f"маршрутизаторы={len(config.routers)} services={len(config.services)} "
            f"mappings={len(config.mappings)}"
        )
        self._console.print(f"secrets_env_file={config.runtime.secrets_env_file}")
        self._pause()

    def _about_menu(self) -> None:
        self._console.print(f"[bold]{panel_formatting.ICON_APP} FQDN-updater panel[/bold]")
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

    def _load_service_entry_counts(
        self,
        *,
        config: AppConfig,
    ) -> dict[str, panel_formatting.ServiceEntryCounts]:
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
    ) -> panel_router_support.MappingPlan | None:
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
        default_target: panel_router_support.RouteTargetDraft,
        missing_secret_message: str | None,
        discovery_password: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> panel_router_support.RouteTargetDraft | None:
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
    ) -> panel_router_support.RouteTargetDraft | None:
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
        default_target: panel_router_support.RouteTargetDraft,
        hint_lines: tuple[str, ...] | None = None,
    ) -> panel_router_support.RouteTargetDraft | None:
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
        mapping_plan: panel_router_support.MappingPlan | None,
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
                panel_formatting._format_connected(candidate.connected),
                candidate.status or "[dim]-[/dim]",
                candidate.detail or "[dim]-[/dim]",
            )
        self._console.print(
            Panel(
                table,
                title=panel_formatting._icon_label(
                    panel_formatting.ICON_SEARCH, "WireGuard discovery"
                ),
                border_style="cyan",
            )
        )

    def _render_summary(self, *, title: str, rows: list[tuple[str, str]]) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold white")
        table.add_column(style="bright_cyan")
        for label, value in rows:
            table.add_row(label, value)
        icon = (
            panel_formatting.ICON_PASSWORD
            if "пароль" in title.lower()
            else panel_formatting.ICON_SUMMARY
        )
        self._console.print(
            Panel(
                table,
                title=panel_formatting._icon_label(icon, title),
                border_style="bright_cyan",
            )
        )

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
                (f"{panel_formatting.ICON_WARNING} WireGuard discovery не прошёл: ", "yellow"),
                (panel_formatting._truncate_discovery_error_message(message), "red"),
            )
        )

    def _print_router_connectivity_error(self) -> None:
        self._console.print(
            f"[red]{panel_formatting.ICON_ERROR} Проверка связи с маршрутизатором не прошла.[/red]"
        )

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
        return f"cat {panel_formatting._shell_quote_path(candidate)}"

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
    router = panel_formatting._find_router(config=config, router_id=router_id)
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


def _menu_choice(
    icon: str,
    title: str,
    value: str,
    *,
    disabled: str | None = None,
) -> PromptChoice:
    return PromptChoice(
        title=panel_formatting._icon_label(icon, title),
        value=value,
        disabled=disabled,
        answer_title=title,
    )
