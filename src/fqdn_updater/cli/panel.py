from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fqdn_updater.application.config_bootstrap import ConfigBootstrapService
from fqdn_updater.application.config_management import (
    ConfigManagementService,
    normalize_rci_url_input,
)
from fqdn_updater.application.dry_run_orchestration import (
    DryRunExecutionResult,
    DryRunOrchestrator,
)
from fqdn_updater.application.password_generation import RciPasswordGenerator
from fqdn_updater.application.route_target_discovery import RouteTargetDiscoveryService
from fqdn_updater.application.run_history import RunHistoryResult, RunHistoryService
from fqdn_updater.application.service_sync_planning import ServiceSyncPlanner
from fqdn_updater.application.source_loading import SourceLoadingService
from fqdn_updater.application.status_diagnostics import StatusDiagnosticsService
from fqdn_updater.cli.panel_prompts import PromptAdapter, PromptChoice, QuestionaryPromptAdapter
from fqdn_updater.domain.config_schema import (
    AppConfig,
    RouterConfig,
    RouterServiceMappingConfig,
)
from fqdn_updater.domain.keenetic import RouteTargetCandidate
from fqdn_updater.domain.run_artifact import RunArtifact, RunStatus, RunTrigger
from fqdn_updater.domain.status_diagnostics import StatusDiagnosticsResult
from fqdn_updater.infrastructure.config_repository import ConfigRepository
from fqdn_updater.infrastructure.keenetic_rci_client import KeeneticRciClientFactory
from fqdn_updater.infrastructure.raw_source_fetcher import HttpRawSourceFetcher
from fqdn_updater.infrastructure.router_secret_resolver import EnvironmentFileSecretResolver
from fqdn_updater.infrastructure.run_artifact_repository import RunArtifactRepository
from fqdn_updater.infrastructure.run_logging import RunLoggerFactory
from fqdn_updater.infrastructure.secret_env_file import (
    SecretEnvFile,
    password_env_key_for_router_id,
)

DEFAULT_SELECTED_SERVICES = frozenset(
    {
        "telegram",
        "youtube",
        "discord",
        "twitter",
        "meta",
        "tiktok",
        "google_ai",
    }
)
DEFAULT_INTERFACE_NAME = "Wireguard0"
ROOT_PANEL_WIDTH = 86


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


class PanelController:
    """Interactive terminal control panel for local config management."""

    def __init__(
        self,
        *,
        config_path: Path,
        console: Console | None = None,
        prompts: PromptAdapter | None = None,
    ) -> None:
        self._config_path = config_path
        self._console = console or Console()
        self._prompts = prompts or QuestionaryPromptAdapter(console=self._console)
        self._repository = ConfigRepository()
        self._bootstrap_service = ConfigBootstrapService(repository=self._repository)
        self._management_service = ConfigManagementService(repository=self._repository)
        self._secret_resolver = EnvironmentFileSecretResolver()
        self._client_factory = KeeneticRciClientFactory()
        self._artifact_repository = RunArtifactRepository()
        self._route_target_discovery_service = RouteTargetDiscoveryService(
            secret_resolver=self._secret_resolver,
            client_factory=self._client_factory,
        )
        self._run_history_service = RunHistoryService(repository=self._artifact_repository)
        self._status_service = StatusDiagnosticsService(
            secret_resolver=self._secret_resolver,
            client_factory=self._client_factory,
        )
        self._dry_run_orchestrator = DryRunOrchestrator(
            source_loader=SourceLoadingService(fetcher=HttpRawSourceFetcher()),
            secret_resolver=self._secret_resolver,
            client_factory=self._client_factory,
            planner=ServiceSyncPlanner(),
            artifact_writer=self._artifact_repository,
            logger_factory=RunLoggerFactory(),
        )
        self._password_generator = RciPasswordGenerator()

    def run(self) -> None:
        self._ensure_config_exists()
        while True:
            config = self._load_config()
            self._render_dashboard(config=config)
            choice = self._prompts.select(
                message="Выберите раздел панели",
                choices=[
                    PromptChoice("Маршрутизаторы", "routers"),
                    PromptChoice("Списки и маршруты", "lists"),
                    PromptChoice("Прогоны и диагностика", "runs"),
                    PromptChoice("Проверка конфига", "config"),
                    PromptChoice("О панели", "about"),
                    PromptChoice("Выход", "exit"),
                ],
                default="routers",
                instruction="Стрелки выбирают, Enter подтверждает, Ctrl+C отменяет.",
            )
            if choice in {None, "exit"}:
                return
            if choice == "routers":
                self._router_menu()
            elif choice == "lists":
                self._lists_menu()
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
            raise RuntimeError(f"Config file does not exist: {self._config_path}")
        self._bootstrap_service.create_default_config(path=self._config_path)
        self._console.print(f"[green]Scaffold config создан:[/green] {self._config_path}")

    def _load_config(self) -> AppConfig:
        return self._repository.load(path=self._config_path)

    def _render_dashboard(self, *, config: AppConfig) -> None:
        self._console.clear()
        title = Text("FQDN-updater", style="bold white")
        subtitle = Text("операторская панель", style="bold cyan")
        header = Text.assemble(title, "  ", subtitle)
        self._console.print(Panel(header, border_style="bright_cyan", width=ROOT_PANEL_WIDTH))

        summary = Table.grid(padding=(0, 2))
        summary.add_column(style="bold white")
        summary.add_column(style="bright_cyan")
        summary.add_row("Config", str(self._config_path))
        summary.add_row("Secrets", str(self._secrets_env_path(config=config)))
        summary.add_row("Routers", str(len(config.routers)))
        summary.add_row("Mappings", str(len(config.mappings)))
        summary.add_row("Artifacts", config.runtime.artifacts_dir)
        summary.add_row("Logs", config.runtime.logs_dir)
        self._console.print(
            Panel(
                summary,
                title="Рабочий контекст",
                border_style="cyan",
                width=ROOT_PANEL_WIDTH,
            )
        )

        router_table = Table(show_header=True, header_style="bold white", box=None)
        router_table.add_column("Маршрутизатор")
        router_table.add_column("Статус")
        router_table.add_column("Сервисы")
        router_table.add_column("RCI")
        for router in config.routers:
            selected_services = sorted(
                mapping.service_key for mapping in config.mappings if mapping.router_id == router.id
            )
            router_table.add_row(
                router.id,
                _router_state_label(router.enabled),
                ", ".join(selected_services) if selected_services else "[dim]нет[/dim]",
                str(router.rci_url),
            )
        if not config.routers:
            router_table.add_row("[dim]нет[/dim]", "-", "-", "-")
        self._console.print(
            Panel(
                router_table,
                title="Маршрутизаторы",
                border_style="bright_black",
                width=ROOT_PANEL_WIDTH,
            )
        )

    def _router_menu(self) -> None:
        while True:
            config = self._load_config()
            has_routers = bool(config.routers)
            choice = self._prompts.select(
                message="Маршрутизаторы",
                choices=[
                    PromptChoice("Добавить новый роутер", "add"),
                    PromptChoice(
                        "Изменить параметры существующего роутера",
                        "edit",
                        disabled=None if has_routers else "Нет настроенных роутеров",
                    ),
                    PromptChoice(
                        "Включить или выключить роутер",
                        "toggle",
                        disabled=None if has_routers else "Нет настроенных роутеров",
                    ),
                    PromptChoice(
                        "Повернуть пароль RCI",
                        "rotate",
                        disabled=None if has_routers else "Нет настроенных роутеров",
                    ),
                    PromptChoice("Назад", "back"),
                ],
                default="add",
            )
            if choice in {None, "back"}:
                return
            if choice == "add":
                self._add_router()
            elif choice == "edit":
                self._edit_router()
            elif choice == "toggle":
                self._toggle_router_enabled()
            elif choice == "rotate":
                self._rotate_router_password()

    def _add_router(self) -> None:
        config = self._load_config()
        router_id = self._prompts.text(
            message="Router id",
            instruction="Используйте lowercase, цифры, дефисы и подчёркивания.",
        )
        if router_id is None:
            return
        router_id = router_id.strip()

        existing_router = _find_router(config=config, router_id=router_id)
        if existing_router is not None:
            self._console.print(
                "[yellow]"
                f"Router '{router_id}' уже существует. Используйте режим редактирования."
                "[/yellow]"
            )
            self._pause()
            return

        name = self._prompts.text(message="Имя роутера", default=router_id)
        if name is None:
            return
        rci_url = self._prompts.text(message="KeenDNS RCI URL", default="")
        if rci_url is None:
            return
        username = self._prompts.text(message="RCI username", default="api_updater")
        if username is None:
            return
        timeout_seconds = self._prompt_timeout_seconds(default=10)
        if timeout_seconds is None:
            return

        password_env = password_env_key_for_router_id(router_id)
        _ensure_password_env_available(
            config=config,
            router_id=router_id,
            password_env=password_env,
        )

        try:
            draft_router = RouterConfig.model_validate(
                {
                    "id": router_id,
                    "name": name,
                    "rci_url": normalize_rci_url_input(rci_url),
                    "username": username,
                    "password_env": password_env,
                    "password_file": None,
                    "enabled": True,
                    "tags": [],
                    "timeout_seconds": timeout_seconds,
                    "allowed_source_ips": [],
                }
            )
        except ValidationError as exc:
            self._console.print(
                f"[yellow]Router input is invalid:[/yellow] {_format_validation_error(exc)}"
            )
            self._pause()
            return

        selected_services = self._prompt_service_selection(
            config=config,
            selected=DEFAULT_SELECTED_SERVICES,
        )
        if selected_services is None:
            return

        mapping_plan = None
        if selected_services:
            mapping_plan = self._prompt_mapping_plan(
                config=config,
                router=draft_router,
                editable_mappings=[],
                selected_services=selected_services,
                missing_secret_message=(
                    "Автообнаружение WireGuard пропущено: сначала сохраните роутер, "
                    "примените пароль на Keenetic и затем откройте мастер списков снова."
                ),
            )
            if mapping_plan is None:
                return

        password = self._password_generator.generate()
        new_mappings = self._build_router_mappings(
            router_id=router_id,
            selected_services=selected_services,
            existing_mappings={},
            mapping_plan=mapping_plan,
        )

        self._render_summary(
            title="Проверка сохранения",
            rows=[
                ("Операция", "добавить роутер"),
                ("Router", router_id),
                ("Имя", name),
                ("RCI URL", normalize_rci_url_input(rci_url)),
                ("Username", username),
                ("Timeout", str(timeout_seconds)),
                ("Password env", password_env),
                ("Сервисы", ", ".join(sorted(selected_services)) or "нет"),
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
            ],
        )
        should_save = self._prompts.confirm(message="Сохранить новый роутер?", default=True)
        if not should_save:
            return

        self._management_service.save_router_setup(
            path=self._config_path,
            router_id=router_id,
            name=name,
            rci_url=rci_url,
            username=username,
            password_env=password_env,
            password_file=None,
            enabled=True,
            tags=[],
            timeout_seconds=timeout_seconds,
            allowed_source_ips=[],
            replace_mappings=new_mappings,
        )
        try:
            SecretEnvFile(path=self._secrets_env_path(config=config)).write_value(
                key=password_env,
                value=password,
            )
        except Exception:
            self._repository.overwrite(path=self._config_path, config=config)
            raise

        self._console.print("[green]Роутер сохранён.[/green]")
        self._show_generated_password(password=password)
        self._pause()

    def _edit_router(self) -> None:
        config = self._load_config()
        router = self._select_router(config=config, message="Выберите роутер для редактирования")
        if router is None:
            return

        name = self._prompts.text(message="Имя роутера", default=router.name)
        if name is None:
            return
        rci_url = self._prompts.text(message="KeenDNS RCI URL", default=str(router.rci_url))
        if rci_url is None:
            return
        username = self._prompts.text(message="RCI username", default=router.username)
        if username is None:
            return
        timeout_seconds = self._prompt_timeout_seconds(default=router.timeout_seconds)
        if timeout_seconds is None:
            return

        try:
            RouterConfig.model_validate(
                {
                    "id": router.id,
                    "name": name,
                    "rci_url": normalize_rci_url_input(rci_url),
                    "username": username,
                    "password_env": router.password_env,
                    "password_file": router.password_file,
                    "enabled": router.enabled,
                    "tags": list(router.tags),
                    "timeout_seconds": timeout_seconds,
                    "allowed_source_ips": list(router.allowed_source_ips),
                }
            )
        except ValidationError as exc:
            self._console.print(
                f"[yellow]Router input is invalid:[/yellow] {_format_validation_error(exc)}"
            )
            self._pause()
            return

        self._render_summary(
            title="Проверка сохранения",
            rows=[
                ("Операция", "изменить роутер"),
                ("Router", router.id),
                ("Имя", name),
                ("RCI URL", normalize_rci_url_input(rci_url)),
                ("Username", username),
                ("Timeout", str(timeout_seconds)),
                ("Статус", "enabled" if router.enabled else "disabled"),
                ("Password ref", router.password_env or router.password_file or "нет"),
            ],
        )
        should_save = self._prompts.confirm(message="Сохранить изменения роутера?", default=True)
        if not should_save:
            return

        self._management_service.replace_router(
            path=self._config_path,
            router_id=router.id,
            name=name,
            rci_url=rci_url,
            username=username,
            password_env=router.password_env,
            password_file=router.password_file,
            enabled=router.enabled,
            tags=list(router.tags),
            timeout_seconds=timeout_seconds,
            allowed_source_ips=list(router.allowed_source_ips),
        )
        self._console.print("[green]Параметры роутера обновлены.[/green]")
        self._pause()

    def _toggle_router_enabled(self) -> None:
        config = self._load_config()
        router = self._select_router(
            config=config,
            message="Выберите роутер для включения или выключения",
        )
        if router is None:
            return

        next_enabled = not router.enabled
        self._render_summary(
            title="Проверка сохранения",
            rows=[
                ("Операция", "включить роутер" if next_enabled else "выключить роутер"),
                ("Router", router.id),
                ("Текущее состояние", _router_state_plain(router.enabled)),
                ("Новое состояние", _router_state_plain(next_enabled)),
                ("Password ref", router.password_env or router.password_file or "нет"),
            ],
        )
        should_save = self._prompts.confirm(
            message="Сохранить новый статус роутера?",
            default=True,
        )
        if not should_save:
            return

        self._management_service.replace_router(
            path=self._config_path,
            router_id=router.id,
            name=router.name,
            rci_url=str(router.rci_url),
            username=router.username,
            password_env=router.password_env,
            password_file=router.password_file,
            enabled=next_enabled,
            tags=list(router.tags),
            timeout_seconds=router.timeout_seconds,
            allowed_source_ips=list(router.allowed_source_ips),
        )
        self._console.print("[green]Статус роутера обновлён.[/green]")
        self._pause()

    def _rotate_router_password(self) -> None:
        config = self._load_config()
        router = self._select_router(config=config, message="Выберите роутер для смены пароля")
        if router is None:
            return

        password_env = router.password_env or password_env_key_for_router_id(router.id)
        password = self._password_generator.generate()
        _ensure_password_env_available(
            config=config,
            router_id=router.id,
            password_env=password_env,
        )
        self._render_summary(
            title="Проверка смены пароля",
            rows=[
                ("Операция", "rotate password"),
                ("Router", router.id),
                ("Password env", password_env),
                (
                    "Password file",
                    (
                        f"{router.password_file} (будет очищен)"
                        if router.password_file is not None
                        else "нет"
                    ),
                ),
            ],
        )
        should_rotate = self._prompts.confirm(
            message="Повернуть пароль RCI для этого роутера?",
            default=False,
        )
        if not should_rotate:
            return

        if router.password_env != password_env or router.password_file is not None:
            self._management_service.update_router_secret_reference(
                path=self._config_path,
                router_id=router.id,
                password_env=password_env,
                password_file=None,
            )
        try:
            SecretEnvFile(path=self._secrets_env_path(config=config)).write_value(
                key=password_env,
                value=password,
            )
        except Exception:
            self._repository.overwrite(path=self._config_path, config=config)
            raise

        self._console.print("[green]Пароль роутера обновлён.[/green]")
        self._show_generated_password(password=password)
        self._pause()

    def _lists_menu(self) -> None:
        config = self._load_config()
        router = self._select_router(
            config=config, message="Выберите роутер для списков и маршрутов"
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
                ("Router", router.id),
                ("Добавить", ", ".join(added_services) or "нет"),
                ("Удалить", ", ".join(removed_services) or "нет"),
                ("Оставить", ", ".join(kept_services) or "нет"),
                ("Сервисы после сохранения", ", ".join(sorted(selected_services)) or "нет"),
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
            message="Сохранить списки и маршруты для роутера?",
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
        self._pause()

    def _runs_menu(self) -> None:
        while True:
            config = self._load_config()
            history = self._run_history_service.list_recent(
                config=config,
                config_path=self._config_path,
                limit=8,
            )
            self._render_runs_screen(config=config, history=history)
            choice = self._prompts.select(
                message="Прогоны и диагностика",
                choices=[
                    PromptChoice("Статус и диагностика (read-only сеть)", "status"),
                    PromptChoice("Dry-run preview (read-only, пишет artifact/log)", "dry-run"),
                    PromptChoice("Назад", "back"),
                ],
                default="status",
            )
            if choice in {None, "back"}:
                return
            if choice == "status":
                self._run_status_diagnostics()
            elif choice == "dry-run":
                self._run_dry_run_preview()

    def _config_menu(self, *, config: AppConfig) -> None:
        self._console.print("[green]Конфиг валиден.[/green]")
        self._console.print(
            f"routers={len(config.routers)} services={len(config.services)} "
            f"mappings={len(config.mappings)}"
        )
        self._console.print(f"secrets_env_file={config.runtime.secrets_env_file}")
        self._pause()

    def _about_menu(self) -> None:
        self._console.print("[bold]FQDN-updater panel[/bold]")
        self._console.print("Локальная терминальная панель для управления config и preview-flow.")
        self._console.print(
            "Изменения на роутерах выполняются только через dry-run/sync с явным выбором."
        )
        self._pause()

    def _select_router(self, *, config: AppConfig, message: str) -> RouterConfig | None:
        if not config.routers:
            self._console.print("[yellow]Нет настроенных роутеров.[/yellow]")
            self._pause()
            return None

        choices = [
            PromptChoice(
                title=f"{router.id} | {router.name} | {_router_state_plain(router.enabled)}",
                value=router.id,
            )
            for router in config.routers
        ]
        selected_router_id = self._prompts.select(
            message=message,
            choices=choices,
            default=config.routers[0].id,
        )
        if selected_router_id is None:
            return None
        return _find_router(config=config, router_id=selected_router_id)

    def _prompt_service_selection(
        self,
        *,
        config: AppConfig,
        selected: set[str],
    ) -> set[str] | None:
        service_choices = [
            PromptChoice(
                title=service.key,
                value=service.key,
                checked=service.key in selected,
            )
            for service in config.services
            if service.enabled
        ]
        if not service_choices:
            self._console.print("[yellow]Нет enabled сервисов для выбора.[/yellow]")
            self._pause()
            return set()

        result = self._prompts.checkbox(
            message="Выберите управляемые сервисы",
            choices=service_choices,
            instruction="Пробел переключает, Enter сохраняет набор, Ctrl+C отменяет.",
        )
        if result is None:
            return None
        return set(result)

    def _prompt_timeout_seconds(self, *, default: int) -> int | None:
        while True:
            raw_value = self._prompts.text(
                message="RCI timeout seconds",
                default=str(default),
            )
            if raw_value is None:
                return None
            stripped_value = raw_value.strip()
            try:
                parsed_value = int(stripped_value)
            except ValueError:
                self._console.print("[yellow]Timeout должен быть целым числом.[/yellow]")
                continue
            if parsed_value < 1:
                self._console.print("[yellow]Timeout должен быть не меньше 1 секунды.[/yellow]")
                continue
            return parsed_value

    def _prompt_mapping_plan(
        self,
        *,
        config: AppConfig,
        router: RouterConfig,
        editable_mappings: list[RouterServiceMappingConfig],
        selected_services: set[str],
        missing_secret_message: str | None,
    ) -> MappingPlan | None:
        default_target, has_inconsistent_default, google_ai_override = (
            _derive_mapping_plan_defaults(
                editable_mappings=editable_mappings,
            )
        )
        if has_inconsistent_default:
            self._console.print(
                "[yellow]У текущих non-google_ai mappings разные route targets. "
                "После сохранения они будут нормализованы к одному базовому target.[/yellow]"
            )

        default_target = self._prompt_route_target(
            config=config,
            router=router,
            label="Базовый route target",
            default_target=default_target,
            missing_secret_message=missing_secret_message,
        )
        if default_target is None:
            return None

        google_ai_target = None
        has_non_google_services = any(
            service_key != "google_ai" for service_key in selected_services
        )
        if "google_ai" in selected_services and has_non_google_services:
            use_override = self._prompts.confirm(
                message="Использовать отдельный route target для google_ai?",
                default=google_ai_override is not None,
            )
            if use_override is None:
                return None
            if use_override:
                google_ai_target = self._prompt_route_target(
                    config=config,
                    router=router,
                    label="Route target для google_ai",
                    default_target=google_ai_override or default_target,
                    missing_secret_message=missing_secret_message,
                )
                if google_ai_target is None:
                    return None

        return MappingPlan(default_target=default_target, google_ai_target=google_ai_target)

    def _prompt_route_target(
        self,
        *,
        config: AppConfig,
        router: RouterConfig,
        label: str,
        default_target: RouteTargetDraft,
        missing_secret_message: str | None,
    ) -> RouteTargetDraft | None:
        target_type = self._prompts.select(
            message=f"{label}: тип маршрута",
            choices=[
                PromptChoice("Интерфейс Keenetic", "interface"),
                PromptChoice("Шлюз", "gateway"),
            ],
            default=default_target.route_target_type,
        )
        if target_type is None:
            return None

        if target_type == "interface":
            return self._prompt_interface_target(
                config=config,
                router=router,
                label=label,
                default_value=default_target.route_target_value,
                missing_secret_message=missing_secret_message,
            )
        return self._prompt_gateway_target(label=label, default_target=default_target)

    def _prompt_interface_target(
        self,
        *,
        config: AppConfig,
        router: RouterConfig,
        label: str,
        default_value: str,
        missing_secret_message: str | None,
    ) -> RouteTargetDraft | None:
        candidates = self._discover_route_targets(
            config=config,
            router=router,
            missing_secret_message=missing_secret_message,
        )
        if candidates:
            self._render_route_target_candidates(candidates=candidates)
            choices = [
                PromptChoice(
                    title=_route_candidate_title(candidate),
                    value=candidate.value,
                )
                for candidate in candidates
            ]
            choices.append(PromptChoice("Ввести интерфейс вручную", "manual"))
            default_choice = (
                default_value
                if any(candidate.value == default_value for candidate in candidates)
                else "manual"
            )
            selected_value = self._prompts.select(
                message=label,
                choices=choices,
                default=default_choice,
            )
            if selected_value is None:
                return None
            if selected_value != "manual":
                return RouteTargetDraft(
                    route_target_type="interface",
                    route_target_value=selected_value,
                    route_interface=None,
                )

        manual_value = self._prompts.text(
            message=f"{label}: имя интерфейса Keenetic",
            default=default_value,
        )
        if manual_value is None:
            return None
        normalized_value = manual_value.strip() or default_value
        return RouteTargetDraft(
            route_target_type="interface",
            route_target_value=normalized_value,
            route_interface=None,
        )

    def _prompt_gateway_target(
        self,
        *,
        label: str,
        default_target: RouteTargetDraft,
    ) -> RouteTargetDraft | None:
        default_gateway = (
            default_target.route_target_value
            if default_target.route_target_type == "gateway"
            else ""
        )
        default_interface = default_target.route_interface or ""

        gateway_value = self._prompts.text(
            message=f"{label}: IP или адрес шлюза",
            default=default_gateway,
        )
        if gateway_value is None:
            return None
        route_interface = self._prompts.text(
            message=f"{label}: интерфейс для gateway route (опционально)",
            default=default_interface,
            instruction="Оставьте пустым, если интерфейс не нужен.",
        )
        if route_interface is None:
            return None

        normalized_gateway = gateway_value.strip()
        normalized_interface = route_interface.strip() or None
        return RouteTargetDraft(
            route_target_type="gateway",
            route_target_value=normalized_gateway,
            route_interface=normalized_interface,
        )

    def _build_router_mappings(
        self,
        *,
        router_id: str,
        selected_services: set[str],
        existing_mappings: dict[str, RouterServiceMappingConfig],
        mapping_plan: MappingPlan | None,
    ) -> list[dict[str, Any]]:
        if not selected_services:
            return []
        if mapping_plan is None:
            raise RuntimeError("mapping plan is required when services are selected")

        mappings: list[dict[str, Any]] = []
        for service_key in sorted(selected_services):
            existing_mapping = existing_mappings.get(service_key)
            target = mapping_plan.target_for_service(service_key)
            mappings.append(
                {
                    "router_id": router_id,
                    "service_key": service_key,
                    "object_group_name": (
                        existing_mapping.object_group_name
                        if existing_mapping is not None
                        else f"fqdn-{service_key}"
                    ),
                    "route_target_type": target.route_target_type,
                    "route_target_value": target.route_target_value,
                    "route_interface": target.route_interface,
                    "exclusive": (
                        existing_mapping.exclusive if existing_mapping is not None else True
                    ),
                    "auto": existing_mapping.auto if existing_mapping is not None else True,
                    "managed": existing_mapping.managed if existing_mapping is not None else True,
                }
            )
        return mappings

    def _discover_route_targets(
        self,
        *,
        config: AppConfig,
        router: RouterConfig | None,
        missing_secret_message: str | None = None,
    ) -> tuple[RouteTargetCandidate, ...]:
        if router is None:
            return ()

        try:
            SecretEnvFile(path=self._secrets_env_path(config=config)).load_into_environment()
        except RuntimeError as exc:
            self._console.print(f"[yellow]WireGuard discovery failed:[/yellow] {exc}")
            return ()

        result = self._route_target_discovery_service.discover_wireguard_targets(router=router)
        if result.error_message is not None:
            if missing_secret_message is not None and _is_missing_password_env_error(
                result.error_message
            ):
                self._console.print(f"[yellow]{missing_secret_message}[/yellow]")
                return ()
            self._console.print(
                f"[yellow]WireGuard discovery failed:[/yellow] {result.error_message}"
            )
            return ()
        if not result.candidates:
            self._console.print("[yellow]WireGuard интерфейсы не обнаружены.[/yellow]")
            return ()
        return result.candidates

    def _render_runs_screen(self, *, config: AppConfig, history: RunHistoryResult) -> None:
        self._console.print(
            Panel(
                "[bold]Прогоны и диагностика[/bold]",
                border_style="bright_cyan",
                width=ROOT_PANEL_WIDTH,
            )
        )

        context = Table.grid(padding=(0, 2))
        context.add_column(style="bold white")
        context.add_column(style="bright_cyan")
        context.add_row("Артефакты", self._display_path(history.artifacts_dir))
        context.add_row("Логи", self._display_path(config.runtime.logs_dir))
        context.add_row("Показано", str(len(history.runs)))
        context.add_row("Предупреждения", str(len(history.warnings)))
        self._console.print(
            Panel(
                context,
                title="Контекст прогонов",
                border_style="cyan",
                width=ROOT_PANEL_WIDTH,
            )
        )

        table = Table(show_header=True, header_style="bold white", box=None)
        table.add_column("Run")
        table.add_column("Режим")
        table.add_column("Статус")
        table.add_column("Завершён")
        table.add_column("Роутеры")
        table.add_column("Summary")
        for run in history.runs:
            artifact = run.artifact
            table.add_row(
                artifact.run_id,
                artifact.mode.value,
                _format_run_status(artifact.status),
                artifact.finished_at.isoformat(),
                str(len(artifact.router_results)),
                _format_artifact_summary(artifact),
            )
        if not history.runs:
            table.add_row("[dim]нет[/dim]", "-", "-", "-", "-", "-")
        self._console.print(
            Panel(
                table,
                title="Последние локальные артефакты",
                border_style="bright_black",
                width=ROOT_PANEL_WIDTH,
            )
        )

        if history.warnings:
            warning_table = Table(show_header=True, header_style="bold white", box=None)
            warning_table.add_column("Файл")
            warning_table.add_column("Причина")
            for warning in history.warnings[:3]:
                warning_table.add_row(self._display_path(warning.path), warning.message)
            if len(history.warnings) > 3:
                omitted_count = len(history.warnings) - 3
                warning_table.add_row(
                    f"+{omitted_count} ещё",
                    "Список предупреждений сокращён в панели",
                )
            self._console.print(
                Panel(
                    warning_table,
                    title="Пропущенные артефакты",
                    border_style="yellow",
                    width=ROOT_PANEL_WIDTH,
                )
            )

        command = Table.grid(padding=(0, 2))
        command.add_column(style="bold white")
        command.add_column(style="bright_cyan")
        command.add_row("Команда", "fqdn-updater sync --config")
        command.add_row("Config", str(self._config_path))
        self._console.print(
            Panel(
                command,
                title="Ручной запуск",
                border_style="cyan",
                width=ROOT_PANEL_WIDTH,
            )
        )

    def _run_status_diagnostics(self) -> None:
        config = self._config_with_resolved_runtime_paths(config=self._load_config())
        try:
            self._load_runtime_secret_env_file(config=config)
            result = self._status_service.check(config=config)
        except RuntimeError as exc:
            self._console.print(f"[red]Status diagnostics failed:[/red] {exc}")
            self._pause()
            return

        self._render_status_result(result=result)
        self._pause()

    def _run_dry_run_preview(self) -> None:
        config = self._config_with_resolved_runtime_paths(config=self._load_config())
        try:
            self._load_runtime_secret_env_file(config=config)
            result = self._dry_run_orchestrator.run(
                config=config,
                trigger=RunTrigger.MANUAL,
            )
        except RuntimeError as exc:
            self._console.print(f"[red]Dry-run preview failed:[/red] {exc}")
            self._pause()
            return

        self._render_dry_run_result(result=result)
        self._pause()

    def _render_status_result(self, *, result: StatusDiagnosticsResult) -> None:
        table = Table(show_header=True, header_style="bold white")
        table.add_column("Роутер")
        table.add_column("Статус")
        table.add_column("DNS proxy")
        table.add_column("Деталь")
        for router in result.router_results:
            table.add_row(
                router.router_id,
                router.status.value,
                _format_dns_proxy(router.dns_proxy_enabled),
                router.error_message or "[dim]-[/dim]",
            )
        if not result.router_results:
            table.add_row("[dim]нет[/dim]", "-", "-", "-")
        title = (
            f"Status diagnostics: overall={result.overall_status.value} "
            f"checked={result.checked_router_count}"
        )
        self._console.print(Panel(table, title=title, border_style="bright_cyan"))

    def _render_dry_run_result(self, *, result: DryRunExecutionResult) -> None:
        artifact = result.artifact
        table = Table(show_header=True, header_style="bold white")
        table.add_column("Роутер")
        table.add_column("Статус")
        table.add_column("Сервисов")
        table.add_column("Summary")
        for router in artifact.router_results:
            changed_services = sum(
                service.added_count > 0 or service.removed_count > 0 or service.route_changed
                for service in router.service_results
            )
            failed_services = sum(
                service.error_message is not None for service in router.service_results
            )
            table.add_row(
                router.router_id,
                router.status.value,
                str(len(router.service_results)),
                f"changed={changed_services} failed={failed_services}",
            )
        if not artifact.router_results:
            table.add_row("[dim]нет[/dim]", "-", "-", "-")

        title = (
            f"Dry-run: run_id={artifact.run_id} status={artifact.status.value} "
            f"artifact={result.artifact_path}"
        )
        self._console.print(Panel(table, title=title, border_style="bright_cyan"))

    def _render_route_target_candidates(
        self,
        *,
        candidates: tuple[RouteTargetCandidate, ...],
    ) -> None:
        table = Table(show_header=True, header_style="bold white")
        table.add_column("Интерфейс")
        table.add_column("Connected")
        table.add_column("Status")
        table.add_column("Detail")
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

    def _pause(self) -> None:
        self._prompts.pause(message="Нажмите любую клавишу для продолжения...")

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
        try:
            return str(candidate.relative_to(self._config_path.parent))
        except ValueError:
            return str(candidate)


def _find_router(*, config: AppConfig, router_id: str) -> RouterConfig | None:
    for router in config.routers:
        if router.id == router_id:
            return router
    return None


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


def _route_candidate_title(candidate: RouteTargetCandidate) -> str:
    return " | ".join(
        (
            candidate.display_name or candidate.value,
            "connected" if candidate.connected else "not connected",
            candidate.status or "-",
            candidate.detail or "-",
        )
    )


def _router_state_label(enabled: bool) -> str:
    if enabled:
        return "[bold green]enabled[/bold green]"
    return "[bold yellow]disabled[/bold yellow]"


def _router_state_plain(enabled: bool) -> str:
    return "enabled" if enabled else "disabled"


def _ensure_password_env_available(
    *,
    config: AppConfig,
    router_id: str,
    password_env: str,
) -> None:
    for router in config.routers:
        if router.id != router_id and router.password_env == password_env:
            raise RuntimeError(
                f"Password env '{password_env}' is already used by router '{router.id}'"
            )


def _format_connected(value: bool | None) -> str:
    if value is None:
        return "[dim]-[/dim]"
    if value:
        return "[green]yes[/green]"
    return "[yellow]no[/yellow]"


def _format_validation_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return str(exc)
    first_error = errors[0]
    location = ".".join(str(part) for part in first_error.get("loc", ()))
    message = str(first_error.get("msg", exc))
    if location:
        return f"{location}: {message}"
    return message


def _is_missing_password_env_error(message: str) -> bool:
    return "password env" in message and "is not set" in message


def _format_run_status(status: RunStatus) -> str:
    if status is RunStatus.SUCCESS:
        return "[green]success[/green]"
    if status is RunStatus.PARTIAL:
        return "[yellow]partial[/yellow]"
    return "[red]failed[/red]"


def _format_artifact_summary(artifact: RunArtifact) -> str:
    changed_services = 0
    failed_services = 0
    for router in artifact.router_results:
        for service in router.service_results:
            if service.error_message is not None:
                failed_services += 1
            if service.added_count > 0 or service.removed_count > 0 or service.route_changed:
                changed_services += 1
    return f"changed={changed_services} failed={failed_services}"


def _format_dns_proxy(value: bool | None) -> str:
    if value is None:
        return "[dim]unknown[/dim]"
    if value:
        return "[green]enabled[/green]"
    return "[yellow]disabled[/yellow]"
