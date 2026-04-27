from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
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
from fqdn_updater.application.sync_orchestration import SyncExecutionResult, SyncOrchestrator
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
    ServiceDefinitionConfig,
)
from fqdn_updater.domain.keenetic import RouteTargetCandidate
from fqdn_updater.domain.run_artifact import (
    RouterResultStatus,
    RunArtifact,
    RunStatus,
    RunTrigger,
)
from fqdn_updater.domain.schedule import RuntimeScheduleConfig, ScheduleWeekday
from fqdn_updater.domain.source_loading import SourceLoadReport
from fqdn_updater.domain.status_diagnostics import StatusDiagnosticsResult
from fqdn_updater.infrastructure.config_repository import ConfigRepository
from fqdn_updater.infrastructure.keenetic_rci_client import KeeneticRciClientFactory
from fqdn_updater.infrastructure.raw_source_fetcher import HttpRawSourceFetcher
from fqdn_updater.infrastructure.router_secret_resolver import EnvironmentFileSecretResolver
from fqdn_updater.infrastructure.run_artifact_repository import RunArtifactRepository
from fqdn_updater.infrastructure.run_lock import FileRunLockManager
from fqdn_updater.infrastructure.run_logging import RunLoggerFactory
from fqdn_updater.infrastructure.secret_env_file import (
    SecretEnvFile,
    password_env_key_for_router_id,
)
from fqdn_updater.infrastructure.service_count_cache import (
    CachingSourceLoadingService,
    ServiceCountCacheRepository,
    ServiceEntryCountSnapshot,
    resolve_service_count_cache_path,
)
from fqdn_updater.infrastructure.systemd_scheduler import SystemdScheduleInstaller

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
DEFAULT_RCI_TIMEOUT_SECONDS = 10
ROOT_PANEL_WIDTH = 86
DISCOVERY_ERROR_MESSAGE_LIMIT = 280
SERVICE_SELECTION_SERVICE_WIDTH = 22
SERVICE_SELECTION_COUNT_WIDTH = 7
KEENETIC_DOMAIN_SELECTION_LIMIT = 1024
SERVICE_SELECTION_GROUPS = {
    "block": (
        "block_p2p_streaming",
        "block_vpn_proxy_privacy",
        "block_dev_hosting_security",
        "block_finance_shopping",
        "block_social_creators",
        "block_news_politics",
        "block_other",
    ),
    "geoblock": (
        "geoblock_ai",
        "geoblock_dev_cloud_saas",
        "geoblock_media_games",
        "geoblock_shopping_travel",
        "geoblock_enterprise_hardware",
        "geoblock_security_networking",
        "geoblock_finance_payments",
        "geoblock_health_reference",
        "geoblock_other",
    ),
    "hodca": (
        "hodca_dev_cloud_saas",
        "hodca_network_os_tools",
        "hodca_media_games",
        "hodca_ai_education_research",
        "hodca_social_lifestyle",
        "hodca_finance_shopping",
        "hodca_other",
    ),
}
SERVICE_DISPLAY_LABELS = {
    "block": "block (full)",
    "block_p2p_streaming": "   p2p/media",
    "block_vpn_proxy_privacy": "   vpn/privacy",
    "block_dev_hosting_security": "   dev/hosting",
    "block_finance_shopping": "   finance/shop",
    "block_social_creators": "   social/media",
    "block_news_politics": "   news/politics",
    "block_other": "   other",
    "geoblock": "geoblock (full)",
    "geoblock_ai": "   AI tools",
    "geoblock_dev_cloud_saas": "   dev/SaaS",
    "geoblock_media_games": "   media/games",
    "geoblock_shopping_travel": "   shopping/travel",
    "geoblock_enterprise_hardware": "   enterprise",
    "geoblock_security_networking": "   security/net",
    "geoblock_finance_payments": "   payments",
    "geoblock_health_reference": "   health/ref",
    "geoblock_other": "   other",
    "hodca": "H.O.D.C.A (full)",
    "hodca_dev_cloud_saas": "   dev/cloud/SaaS",
    "hodca_network_os_tools": "   network/OS/tools",
    "hodca_media_games": "   media/games",
    "hodca_ai_education_research": "   AI/education",
    "hodca_social_lifestyle": "   social/lifestyle",
    "hodca_finance_shopping": "   finance/shop",
    "hodca_other": "   other",
    "meta": "meta (whatsapp)",
}
MAIN_MENU_HINT_LINES = (
    "Для начала работы добавьте маршрутизатор Keenetic с ОС версии 5 и выше.",
    "Затем настройте обновление списков по расписанию.",
)
SCHEDULE_MENU_HINT_LINES = (
    "Расписание хранится в config и разворачивается только через systemd timer.",
    "Команда установки обновляет host-level unit/timer из сохранённых параметров.",
)
MANUAL_RUN_HINT_LINES = (
    "Dry-run проверит план без записи в Keenetic; sync сразу применит сохранённые mappings.",
    "Для sync выберите один или несколько маршрутизаторов для немедленного обновления списков.",
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
SCHEDULE_WEEKDAY_TITLES = {
    ScheduleWeekday.MON: "Понедельник (mon)",
    ScheduleWeekday.TUE: "Вторник (tue)",
    ScheduleWeekday.WED: "Среда (wed)",
    ScheduleWeekday.THU: "Четверг (thu)",
    ScheduleWeekday.FRI: "Пятница (fri)",
    ScheduleWeekday.SAT: "Суббота (sat)",
    ScheduleWeekday.SUN: "Воскресенье (sun)",
}


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
class ServiceEntryCounts:
    domains: int | None
    ipv4: int | None
    ipv6: int | None


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
        self._schedule_installer = SystemdScheduleInstaller()
        self._source_loading_service = SourceLoadingService(fetcher=HttpRawSourceFetcher())
        self._service_count_cache_repository = ServiceCountCacheRepository()
        self._dry_run_orchestrator = DryRunOrchestrator(
            source_loader=self._source_loading_service,
            secret_resolver=self._secret_resolver,
            client_factory=self._client_factory,
            planner=ServiceSyncPlanner(),
            artifact_writer=self._artifact_repository,
            logger_factory=RunLoggerFactory(),
            run_lock_manager=FileRunLockManager(),
        )
        self._sync_orchestrator = SyncOrchestrator(
            source_loader=self._source_loading_service,
            secret_resolver=self._secret_resolver,
            client_factory=self._client_factory,
            planner=ServiceSyncPlanner(),
            artifact_writer=self._artifact_repository,
            logger_factory=RunLoggerFactory(),
            run_lock_manager=FileRunLockManager(),
        )
        self._password_generator = RciPasswordGenerator()

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
        config = self._load_config()
        name = self._prompts.text(
            message="Имя маршрутизатора",
            default="",
            hint_lines=ADD_ROUTER_HINT_LINES,
        )
        if name is None:
            return False
        router_id = _derive_router_id(name=name, config=config)
        username = self._prompts.text(
            message="RCI username",
            default="api_updater",
            hint_lines=ADD_ROUTER_USERNAME_HINT_LINES,
        )
        if username is None:
            return False
        username = username.strip()
        if not username:
            self._console.print("[yellow]RCI username не может быть пустым.[/yellow]")
            self._pause()
            return False

        password_env = password_env_key_for_router_id(router_id)
        _ensure_password_env_available(
            config=config,
            router_id=router_id,
            password_env=password_env,
        )
        password = self._password_generator.generate()
        self._render_summary(
            title="Новый пароль RCI",
            rows=[
                ("Username", username),
                ("Пароль", password),
            ],
        )
        password_ready = self._prompts.confirm(
            message="Пароль уже введён для нового пользователя Keenetic?",
            default=True,
            hint_lines=ADD_ROUTER_PASSWORD_HINT_LINES,
        )
        if password_ready is not True:
            return False

        rci_url = self._prompts.text(
            message="KeenDNS RCI URL",
            default="",
            hint_lines=ADD_ROUTER_RCI_URL_HINT_LINES,
        )
        if rci_url is None:
            return False
        timeout_seconds = DEFAULT_RCI_TIMEOUT_SECONDS

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
                "[yellow]Некорректные параметры маршрутизатора:[/yellow] "
                f"{_format_validation_error(exc)}"
            )
            self._pause()
            return False

        selected_services = self._prompt_service_selection(
            config=config,
            selected=DEFAULT_SELECTED_SERVICES,
            hint_lines=ADD_ROUTER_HINT_LINES,
        )
        if selected_services is None:
            return False

        mapping_plan = None
        if selected_services:
            mapping_plan = self._prompt_mapping_plan(
                config=config,
                router=draft_router,
                editable_mappings=[],
                selected_services=selected_services,
                discovery_password=password,
                hint_lines=ADD_ROUTER_HINT_LINES,
            )
            if mapping_plan is None:
                return False

        new_mappings = self._build_router_mappings(
            router_id=router_id,
            selected_services=selected_services,
            existing_mappings={},
            mapping_plan=mapping_plan,
        )

        self._render_summary(
            title="Проверка сохранения",
            rows=[
                ("Операция", "добавить маршрутизатор"),
                ("Маршрутизатор", router_id),
                ("Имя", name),
                ("RCI URL", normalize_rci_url_input(rci_url)),
                ("Username", username),
                ("Timeout", str(timeout_seconds)),
                ("Password env", password_env),
                ("Сервисы", _format_service_list(sorted(selected_services)) or "нет"),
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
        should_save = self._prompts.confirm(
            message="Сохранить новый маршрутизатор?",
            default=True,
            hint_lines=ADD_ROUTER_SAVE_HINT_LINES,
        )
        if not should_save:
            return False

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

        self._console.print("[green]Маршрутизатор сохранён. Пароль записан в secrets env.[/green]")
        self._pause()
        return True

    def _edit_router(self) -> None:
        config = self._load_config()
        router = self._select_router(
            config=config,
            message="Выберите маршрутизатор для редактирования",
            back_title="Назад",
        )
        if router is None:
            return

        name = self._prompts.text(message="Имя маршрутизатора", default=router.name)
        if name is None:
            return
        rci_url = self._prompts.text(message="KeenDNS RCI URL", default=str(router.rci_url))
        if rci_url is None:
            return
        username = self._prompts.text(message="RCI username", default=router.username)
        if username is None:
            return
        username = username.strip()
        if not username:
            self._console.print("[yellow]RCI username не может быть пустым.[/yellow]")
            self._pause()
            return

        next_password_env = router.password_env or password_env_key_for_router_id(router.id)
        password = self._password_generator.generate()
        self._render_summary(
            title="Новый пароль RCI",
            rows=[
                ("Username", username),
                ("Password env", next_password_env),
                (
                    "Password file",
                    (
                        f"{router.password_file} (будет очищен)"
                        if router.password_file is not None
                        else "нет"
                    ),
                ),
                ("Пароль", password),
            ],
        )
        password_ready = self._prompts.confirm(
            message="Пароль уже обновлён у пользователя Keenetic?",
            default=True,
            hint_lines=EDIT_ROUTER_PASSWORD_HINT_LINES,
        )
        if password_ready is None:
            return
        update_password = password_ready
        if update_password:
            _ensure_password_env_available(
                config=config,
                router_id=router.id,
                password_env=next_password_env,
            )
        password_env = next_password_env if update_password else router.password_env
        password_file = None if update_password else router.password_file
        connectivity_password_override = password if update_password else None
        timeout_seconds = DEFAULT_RCI_TIMEOUT_SECONDS

        try:
            draft_router = RouterConfig.model_validate(
                {
                    "id": router.id,
                    "name": name,
                    "rci_url": normalize_rci_url_input(rci_url),
                    "username": username,
                    "password_env": password_env,
                    "password_file": password_file,
                    "enabled": router.enabled,
                    "tags": list(router.tags),
                    "timeout_seconds": timeout_seconds,
                    "allowed_source_ips": list(router.allowed_source_ips),
                }
            )
        except ValidationError as exc:
            self._console.print(
                "[yellow]Некорректные параметры маршрутизатора:[/yellow] "
                f"{_format_validation_error(exc)}"
            )
            self._pause()
            return

        self._render_summary(
            title="Проверка сохранения",
            rows=[
                ("Операция", "изменить маршрутизатор"),
                ("Маршрутизатор", router.id),
                ("Имя", name),
                ("RCI URL", normalize_rci_url_input(rci_url)),
                ("Username", username),
                ("Timeout", str(timeout_seconds)),
                ("Статус", "включён" if router.enabled else "выключен"),
                ("Password env", password_env or "нет"),
                ("Password file", password_file or "нет"),
            ],
        )
        connectivity_error = self._probe_router_connectivity(
            config=config,
            router=draft_router,
            password_override=connectivity_password_override,
        )
        if connectivity_error is not None:
            self._print_router_connectivity_error()
            self._print_discovery_error(connectivity_error)
        should_save = self._prompts.confirm(
            message="Сохранить изменения маршрутизатора?",
            default=True,
        )
        if not should_save:
            return

        self._management_service.replace_router(
            path=self._config_path,
            router_id=router.id,
            name=name,
            rci_url=rci_url,
            username=username,
            password_env=password_env,
            password_file=password_file,
            enabled=router.enabled,
            tags=list(router.tags),
            timeout_seconds=timeout_seconds,
            allowed_source_ips=list(router.allowed_source_ips),
        )
        if update_password:
            assert password_env is not None
            try:
                SecretEnvFile(path=self._secrets_env_path(config=config)).write_value(
                    key=password_env,
                    value=password,
                )
            except Exception:
                self._repository.overwrite(path=self._config_path, config=config)
                raise
            self._console.print(
                "[green]Параметры маршрутизатора обновлены. Пароль записан в secrets env.[/green]"
            )
        else:
            self._console.print("[green]Параметры маршрутизатора обновлены.[/green]")
        self._pause()

    def _toggle_router_enabled(self) -> None:
        config = self._load_config()
        if not config.routers:
            self._console.print("[yellow]Нет настроенных маршрутизаторов.[/yellow]")
            self._pause()
            return

        router_id_width, router_name_width = _router_selection_column_widths(config.routers)
        selected_enabled_router_ids = self._prompts.checkbox(
            message="Выберите маршрутизаторы, которые должны быть включены",
            choices=[
                PromptChoice(
                    title=_router_toggle_title(
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
                "Стрелки выбирают, Пробел включает или выключает, Enter сохраняет, Esc назад."
            ),
            table_meta=CheckboxTableMeta(
                header=_router_toggle_header(
                    router_id_width=router_id_width,
                    router_name_width=router_name_width,
                ),
                summary=lambda selected_values: _router_toggle_summary(
                    selected_values=selected_values,
                    total=len(config.routers),
                ),
            ),
        )
        if selected_enabled_router_ids is None:
            return

        enabled_router_ids = set(selected_enabled_router_ids)
        current_enabled_router_ids = {router.id for router in config.routers if router.enabled}
        if enabled_router_ids == current_enabled_router_ids:
            return

        payload = config.model_dump(mode="json")
        for router_payload in payload["routers"]:
            router_payload["enabled"] = router_payload["id"] in enabled_router_ids

        updated_config = self._repository.validate_payload(path=self._config_path, payload=payload)
        self._repository.overwrite(
            path=self._config_path,
            config=updated_config,
        )
        self._console.print("[green]Статусы маршрутизаторов обновлены.[/green]")
        self._pause()

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
        page_index = 0
        page_size = 10
        default_choice = "back"
        while True:
            config = self._load_config()
            history = self._run_history_service.list_recent(
                config=config,
                config_path=self._config_path,
                limit=page_size,
                offset=page_index * page_size,
            )
            if not history.runs and history.total_count > 0 and page_index > 0:
                page_index = max(0, (history.total_count - 1) // page_size)
                continue

            has_previous_page = page_index > 0
            has_next_page = (page_index + 1) * page_size < history.total_count
            self._render_runs_screen(
                config=config,
                history=history,
                page_index=page_index,
                page_size=page_size,
            )
            choices: list[PromptChoice] = []
            if has_previous_page:
                choices.append(PromptChoice("Назад", "prev-page"))
            if has_next_page:
                choices.append(PromptChoice("Далее", "next-page"))
            choices.append(PromptChoice("Главное меню", "back"))
            choice = self._prompts.select(
                message="Журнал",
                choices=choices,
                default=(
                    default_choice
                    if any(item.value == default_choice for item in choices)
                    else "back"
                ),
            )
            if choice in {None, "back"}:
                return
            if choice == "prev-page":
                default_choice = "prev-page"
                page_index -= 1
                continue
            if choice == "next-page":
                default_choice = "next-page"
                page_index += 1
                continue

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
        if action == "dry-run":
            self._run_dry_run_preview()
            return

        router_id_width, router_name_width = _router_selection_column_widths(config.routers)
        selected_router_ids = self._prompts.checkbox(
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
        if selected_router_ids is None:
            return
        if not selected_router_ids:
            self._console.print("[yellow]Маршрутизаторы для запуска не выбраны.[/yellow]")
            self._pause()
            return

        self._run_sync_for_routers(router_ids=tuple(selected_router_ids))

    def _schedule_menu(self) -> None:
        while True:
            schedule = self._load_config().runtime.schedule
            self._render_schedule_screen(schedule=schedule)
            choice = self._prompts.select(
                message="Расписание",
                choices=[
                    PromptChoice("Изменить параметры расписания", "edit"),
                    PromptChoice(
                        "Выключить расписание",
                        "disable",
                        disabled=("Расписание уже выключено" if not schedule.is_enabled else None),
                    ),
                    PromptChoice("Установить/обновить в systemd", "install"),
                    PromptChoice("Главное меню", "back"),
                ],
                default="edit",
                hint_lines=SCHEDULE_MENU_HINT_LINES,
            )
            if choice in {None, "back"}:
                return
            if choice == "edit":
                self._edit_schedule()
            elif choice == "disable":
                self._disable_schedule()
            elif choice == "install":
                self._install_schedule()

    def _edit_schedule(self) -> None:
        existing_schedule = self._load_config().runtime.schedule
        mode = self._prompts.select(
            message="Режим расписания",
            choices=[
                PromptChoice("Каждый день", "daily"),
                PromptChoice("По дням недели", "weekly"),
                PromptChoice("Выключить расписание", "disabled"),
                PromptChoice("Назад", "back"),
            ],
            default=existing_schedule.mode.value,
            hint_lines=SCHEDULE_MENU_HINT_LINES,
        )
        if mode in {None, "back"}:
            return
        if mode == "disabled":
            self._disable_schedule()
            return

        default_times = ", ".join(existing_schedule.times) if existing_schedule.times else "03:15"
        time_values = self._prompts.text(
            message="Время запуска (HH:MM, через запятую)",
            default=default_times,
            instruction="Пример: 03:15, 12:00",
        )
        if time_values is None:
            return
        parsed_times = [value.strip() for value in time_values.split(",") if value.strip()]

        selected_weekdays: list[str] = []
        if mode == "weekly":
            weekday_values = self._prompts.checkbox(
                message="Дни недели",
                choices=[
                    PromptChoice(
                        SCHEDULE_WEEKDAY_TITLES[weekday],
                        weekday.value,
                        checked=weekday in existing_schedule.weekdays,
                    )
                    for weekday in ScheduleWeekday
                ],
                instruction=(
                    "Стрелки выбирают, Пробел отмечает, Enter сохраняет набор, Esc назад."
                ),
            )
            if weekday_values is None:
                return
            selected_weekdays = list(weekday_values)

        timezone_value = self._prompts.text(
            message="Timezone IANA",
            default=_schedule_timezone_default(existing_schedule),
            instruction="Например: Europe/Moscow или UTC.",
        )
        if timezone_value is None:
            return

        unit_name = self._prompts.text(
            message="Имя systemd unit",
            default=existing_schedule.systemd.unit_name,
        )
        if unit_name is None:
            return
        deployment_root = self._prompts.text(
            message="Deployment root",
            default=existing_schedule.systemd.deployment_root,
        )
        if deployment_root is None:
            return
        compose_service = self._prompts.text(
            message="Имя docker compose service",
            default=existing_schedule.systemd.compose_service,
        )
        if compose_service is None:
            return

        try:
            schedule = RuntimeScheduleConfig(
                mode=mode,
                times=parsed_times,
                weekdays=selected_weekdays,
                timezone=timezone_value,
                systemd={
                    "unit_name": unit_name,
                    "deployment_root": deployment_root,
                    "compose_service": compose_service,
                },
            )
        except ValidationError as exc:
            self._console.print(f"[red]Расписание не сохранено:[/red] {exc}")
            self._pause()
            return

        self._render_summary(
            title="Проверка расписания",
            rows=[
                ("Режим", schedule.mode.value),
                ("Время", ", ".join(schedule.times)),
                (
                    "Дни",
                    ", ".join(day.value for day in schedule.weekdays) if schedule.weekdays else "-",
                ),
                ("Timezone", schedule.timezone),
                ("Unit", schedule.systemd.unit_name),
                ("Deployment root", schedule.systemd.deployment_root),
                ("Compose service", schedule.systemd.compose_service),
            ],
        )
        should_save = self._prompts.confirm(
            message="Сохранить расписание?",
            default=True,
        )
        if not should_save:
            return

        try:
            self._management_service.replace_schedule(
                path=self._config_path,
                schedule=schedule,
            )
        except RuntimeError as exc:
            self._console.print(f"[red]Не удалось сохранить расписание:[/red] {exc}")
            self._pause()
            return

        self._console.print("[green]Расписание сохранено.[/green]")
        self._pause()

    def _disable_schedule(self) -> None:
        try:
            current_schedule = self._load_config().runtime.schedule
            self._management_service.replace_schedule(
                path=self._config_path,
                schedule=RuntimeScheduleConfig(
                    mode="disabled",
                    times=[],
                    weekdays=[],
                    timezone=current_schedule.timezone,
                    systemd=current_schedule.systemd,
                ),
            )
        except RuntimeError as exc:
            self._console.print(f"[red]Не удалось выключить расписание:[/red] {exc}")
            self._pause()
            return

        self._console.print("[green]Расписание выключено в config.[/green]")
        self._pause()

    def _install_schedule(self) -> None:
        config = self._load_config()
        try:
            result = self._schedule_installer.install(
                config=config,
                config_path=self._config_path,
            )
        except RuntimeError as exc:
            self._console.print(f"[red]Установка расписания не удалась:[/red] {exc}")
            self._pause()
            return

        self._console.print(
            f"[green]systemd units обновлены:[/green] timer_action={result.timer_action}"
        )
        self._console.print(self._display_path(result.service_path))
        self._console.print(self._display_path(result.timer_path))
        self._pause()

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
        if not config.routers:
            self._console.print("[yellow]Нет настроенных маршрутизаторов.[/yellow]")
            self._pause()
            return None

        router_id_width, router_name_width = _router_selection_column_widths(config.routers)
        choices = [
            PromptChoice(
                title=_router_selection_title(
                    router=router,
                    router_id_width=router_id_width,
                    router_name_width=router_name_width,
                ),
                value=router.id,
            )
            for router in config.routers
        ]
        choices.append(PromptChoice(back_title, "__back__"))
        selected_router_id = self._prompts.select(
            message=message,
            choices=choices,
            default=config.routers[0].id,
        )
        if selected_router_id in {None, "__back__"}:
            return None
        return _find_router(config=config, router_id=selected_router_id)

    def _prompt_service_selection(
        self,
        *,
        config: AppConfig,
        selected: set[str],
        hint_lines: tuple[str, ...] | None = None,
    ) -> set[str] | None:
        service_counts = self._load_service_entry_counts(config=config)
        enabled_service_keys = {service.key for service in config.services if service.enabled}
        selection_groups = _enabled_service_selection_groups(enabled_service_keys)
        service_choices = [
            PromptChoice(
                title=_service_selection_title(
                    service_key=service.key,
                    counts=service_counts.get(service.key),
                ),
                value=service.key,
                checked=service.key in selected,
                answer_title=_service_display_label(service.key),
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
            instruction=(
                "Стрелки выбирают, Пробел отмечает, Enter сохраняет набор, Esc возвращает назад."
            ),
            hint_lines=SERVICE_SELECTION_HINT_LINES,
            table_meta=CheckboxTableMeta(
                header=_service_selection_header(),
                summary=lambda selected_values: _service_selection_totals_line(
                    selected_values=selected_values,
                    service_counts=service_counts,
                ),
                selection_groups=selection_groups,
            ),
        )
        if result is None:
            return None
        return _effective_service_selection(result)

    def _load_service_entry_counts(self, *, config: AppConfig) -> dict[str, ServiceEntryCounts]:
        enabled_services = [service for service in config.services if service.enabled]
        if not enabled_services:
            return {}

        cache_path = self._service_count_cache_path(config=config)
        cached_counts = self._service_count_cache_repository.read(path=cache_path)
        enabled_service_keys = {service.key for service in enabled_services}
        if cached_counts and enabled_service_keys.issubset(cached_counts):
            return {
                service.key: _service_entry_counts_from_snapshot(cached_counts.get(service.key))
                for service in enabled_services
            }

        self._console.print("[dim]Считаю текущий размер исходных списков...[/dim]")
        report = self._service_count_source_loader(config=config).load_enabled_services(
            enabled_services
        )
        updated_counts = self._service_count_cache_repository.read(path=cache_path)
        if updated_counts:
            return {
                service.key: _service_entry_counts_from_snapshot(updated_counts.get(service.key))
                for service in enabled_services
            }
        return _service_entry_counts_from_report(
            services=enabled_services,
            report=report,
        )

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
            label="Базовый интерфейс маршрутизации",
            default_target=default_target,
            missing_secret_message=missing_secret_message,
            discovery_password=discovery_password,
            hint_lines=BASE_ROUTE_INTERFACE_HINT_LINES,
        )
        if default_target is None:
            return None

        google_ai_target = None
        has_non_google_services = any(
            service_key != "google_ai" for service_key in selected_services
        )
        if "google_ai" in selected_services and has_non_google_services:
            use_override = self._prompts.confirm(
                message="Использовать отдельный маршрут для google_ai?",
                default=google_ai_override is not None,
                hint_lines=GOOGLE_AI_OVERRIDE_HINT_LINES,
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
                    discovery_password=discovery_password,
                    hint_lines=hint_lines,
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
        discovery_password: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> RouteTargetDraft | None:
        return self._prompt_interface_target(
            config=config,
            router=router,
            label=label,
            default_value=_default_interface_target_value(default_target),
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
        candidates = self._discover_route_targets(
            config=config,
            router=router,
            missing_secret_message=missing_secret_message,
            discovery_password=discovery_password,
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
            choices.append(PromptChoice("Назад", "__back__"))
            default_choice = (
                default_value
                if any(candidate.value == default_value for candidate in candidates)
                else "manual"
            )
            selected_value = self._prompts.select(
                message=label,
                choices=choices,
                default=default_choice,
                hint_lines=hint_lines,
            )
            if selected_value in {None, "__back__"}:
                return None
            if selected_value != "manual":
                return RouteTargetDraft(
                    route_target_type="interface",
                    route_target_value=selected_value,
                    route_interface=None,
                )

        manual_value = self._prompts.text(
            message=label,
            default=default_value,
            instruction="Введите имя интерфейса Keenetic.",
            hint_lines=hint_lines,
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
        hint_lines: tuple[str, ...] | None = None,
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
            hint_lines=hint_lines,
        )
        if gateway_value is None:
            return None
        route_interface = self._prompts.text(
            message=f"{label}: интерфейс для gateway route (опционально)",
            default=default_interface,
            instruction="Оставьте пустым, если интерфейс не нужен.",
            hint_lines=hint_lines,
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
        discovery_password: str | None = None,
    ) -> tuple[RouteTargetCandidate, ...]:
        if router is None:
            return ()

        if discovery_password is None:
            try:
                SecretEnvFile(path=self._secrets_env_path(config=config)).load_into_environment()
            except RuntimeError as exc:
                self._print_discovery_error(str(exc))
                return ()

        result = self._route_target_discovery_service.discover_wireguard_targets(
            router=router,
            password_override=discovery_password,
        )
        if result.error_message is not None:
            if missing_secret_message is not None and _is_missing_password_env_error(
                result.error_message
            ):
                self._console.print(f"[yellow]{missing_secret_message}[/yellow]")
                return ()
            self._print_discovery_error(result.error_message)
            return ()
        if not result.candidates:
            self._console.print("[yellow]WireGuard интерфейсы не обнаружены.[/yellow]")
            return ()
        return result.candidates

    def _probe_router_connectivity(
        self,
        *,
        config: AppConfig,
        router: RouterConfig | None,
        password_override: str | None = None,
    ) -> str | None:
        if router is None:
            return None

        if password_override is None:
            try:
                SecretEnvFile(path=self._secrets_env_path(config=config)).load_into_environment()
            except RuntimeError as exc:
                return str(exc)

        result = self._route_target_discovery_service.discover_wireguard_targets(
            router=router,
            password_override=password_override,
        )
        return result.error_message

    def _render_runs_screen(
        self,
        *,
        config: AppConfig,
        history: RunHistoryResult,
        page_index: int,
        page_size: int,
    ) -> None:
        self._console.clear()
        self._console.print(
            Panel(
                "[bold]Журнал[/bold]",
                border_style="bright_cyan",
                width=ROOT_PANEL_WIDTH,
            )
        )

        context = Table.grid(padding=(0, 2))
        context.add_column(style="bold white")
        context.add_column(style="bright_cyan")
        context.add_row("Артефакты", self._display_path(history.artifacts_dir))
        context.add_row("Логи", self._display_path(config.runtime.logs_dir))
        context.add_row(
            "Страница",
            _format_history_page_label(
                total_count=history.total_count,
                page_index=page_index,
                page_size=page_size,
            ),
        )
        context.add_row(
            "Показано",
            _format_history_page_range(
                total_count=history.total_count,
                page_index=page_index,
                page_size=page_size,
                page_item_count=len(history.runs),
            ),
        )
        context.add_row("Предупреждения", str(len(history.warnings)))
        self._console.print(
            Panel(
                context,
                title="Контекст журнала",
                border_style="cyan",
                width=ROOT_PANEL_WIDTH,
            )
        )

        table = Table(show_header=True, header_style="bold white", box=None)
        table.add_column("Режим", no_wrap=True)
        table.add_column("Запуск", no_wrap=True)
        table.add_column("Статус", no_wrap=True)
        table.add_column("Завершён", no_wrap=True)
        table.add_column("Маршрутизаторы")
        table.add_column("Результат", no_wrap=True)
        for run in history.runs:
            artifact = run.artifact
            table.add_row(
                artifact.mode.value,
                artifact.trigger.value,
                _format_run_status(artifact.status),
                _format_history_finished_at(artifact.finished_at),
                _format_history_router_names(config=config, artifact=artifact),
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

    def _render_schedule_screen(self, *, schedule: RuntimeScheduleConfig) -> None:
        self._console.clear()
        self._console.print(
            Panel(
                "[bold]Расписание[/bold]",
                border_style="bright_cyan",
                width=ROOT_PANEL_WIDTH,
            )
        )
        self._console.print(
            Panel(
                _schedule_summary_table(schedule),
                title="Текущий config",
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
            self._console.print(f"[red]Status diagnostics не прошёл:[/red] {exc}")
            self._pause()
            return

        self._render_status_result(result=result)
        self._pause()

    def _run_dry_run_preview(self) -> None:
        config = self._config_with_resolved_runtime_paths(config=self._load_config())
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
        table = Table(show_header=True, header_style="bold white")
        table.add_column("Маршрутизатор")
        table.add_column("Статус")
        table.add_column("DNS proxy")
        table.add_column("Деталь")
        for router in result.router_results:
            table.add_row(
                router.router_id,
                router.status.value,
                _format_dns_proxy(router.dns_proxy_enabled),
                _format_router_diagnostic_error(router.error_message),
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
        table.add_column("Маршрутизатор")
        table.add_column("Статус")
        table.add_column("Сервисов")
        table.add_column("Итог")
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
                f"изменено={changed_services} ошибок={failed_services}",
            )
        if not artifact.router_results:
            table.add_row("[dim]нет[/dim]", "-", "-", "-")

        title = (
            f"Dry-run: run_id={artifact.run_id} status={artifact.status.value} "
            f"artifact={result.artifact_path}"
        )
        self._console.print(Panel(table, title=title, border_style="bright_cyan"))

    def _render_sync_result(self, *, result: SyncExecutionResult) -> None:
        artifact = result.artifact
        table = Table(show_header=True, header_style="bold white")
        table.add_column("Маршрутизатор")
        table.add_column("Статус")
        table.add_column("Сервисов")
        table.add_column("Итог")
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
                f"изменено={changed_services} ошибок={failed_services}",
            )
        if not artifact.router_results:
            table.add_row("[dim]нет[/dim]", "-", "-", "-")

        title = (
            f"Sync: run_id={artifact.run_id} status={artifact.status.value} "
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
        try:
            return str(candidate.relative_to(self._config_path.parent))
        except ValueError:
            return str(candidate)


def _schedule_summary_table(schedule: RuntimeScheduleConfig) -> Table:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold white")
    table.add_column(style="bright_cyan")
    table.add_row("Режим", schedule.mode.value)
    table.add_row("Время", ", ".join(schedule.times) if schedule.times else "-")
    table.add_row(
        "Дни",
        ", ".join(day.value for day in schedule.weekdays) if schedule.weekdays else "-",
    )
    table.add_row("Timezone", schedule.timezone)
    table.add_row("Unit", schedule.systemd.unit_name)
    return table


def _schedule_timezone_default(schedule: RuntimeScheduleConfig) -> str:
    if schedule.timezone != "UTC" or schedule.is_enabled:
        return schedule.timezone
    current_timezone = datetime.now().astimezone().tzinfo
    timezone_key = getattr(current_timezone, "key", None)
    if isinstance(timezone_key, str) and timezone_key:
        return timezone_key
    return schedule.timezone


def _find_router(*, config: AppConfig, router_id: str) -> RouterConfig | None:
    for router in config.routers:
        if router.id == router_id:
            return router
    return None


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


def _route_candidate_title(candidate: RouteTargetCandidate) -> str:
    return " | ".join(
        (
            candidate.display_name or candidate.value,
            "connected" if candidate.connected else "not connected",
            candidate.status or "-",
            candidate.detail or "-",
        )
    )


def _service_entry_counts_from_report(
    *,
    services: list[ServiceDefinitionConfig],
    report: SourceLoadReport,
) -> dict[str, ServiceEntryCounts]:
    loaded_counts = {
        source.service_key: _service_entry_counts_from_snapshot(
            ServiceEntryCountSnapshot(
                domains=sum(1 for entry in source.typed_entries if entry.kind == "domain"),
                ipv4=sum(1 for entry in source.typed_entries if entry.kind == "ipv4_network"),
                ipv6=sum(1 for entry in source.typed_entries if entry.kind == "ipv6_network"),
            )
        )
        for source in report.loaded
    }
    failed_service_keys = {failure.service_key for failure in report.failed}
    return {
        service.key: (
            ServiceEntryCounts(domains=None, ipv4=None, ipv6=None)
            if service.key in failed_service_keys
            else loaded_counts.get(
                service.key,
                ServiceEntryCounts(domains=None, ipv4=None, ipv6=None),
            )
        )
        for service in services
    }


def _service_entry_counts_from_snapshot(
    snapshot: ServiceEntryCountSnapshot | None,
) -> ServiceEntryCounts:
    if snapshot is None:
        return ServiceEntryCounts(domains=None, ipv4=None, ipv6=None)
    return ServiceEntryCounts(
        domains=snapshot.domains,
        ipv4=snapshot.ipv4,
        ipv6=snapshot.ipv6,
    )


def _service_selection_title(
    *,
    service_key: str,
    counts: ServiceEntryCounts | None,
) -> str:
    counts = counts or ServiceEntryCounts(domains=None, ipv4=None, ipv6=None)
    return (
        f"{_service_display_label(service_key):<{SERVICE_SELECTION_SERVICE_WIDTH}} "
        f"| {_format_entry_count(counts.domains):>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {_format_entry_count(counts.ipv4):>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {_format_entry_count(counts.ipv6):>{SERVICE_SELECTION_COUNT_WIDTH}}"
    )


def _format_entry_count(value: int | None) -> str:
    if value is None:
        return "?"
    return str(value)


def _service_display_label(service_key: str) -> str:
    return SERVICE_DISPLAY_LABELS.get(service_key, service_key)


def _format_service_list(service_keys: Iterable[str]) -> str:
    return ", ".join(_service_display_label(service_key) for service_key in service_keys)


def _service_selection_header() -> str:
    return (
        f"{'Сервис':<{SERVICE_SELECTION_SERVICE_WIDTH}} "
        f"| {'домены':>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {'IPv4':>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {'IPv6':>{SERVICE_SELECTION_COUNT_WIDTH}}"
    )


def _service_selection_totals_line(
    *,
    selected_values: tuple[str, ...],
    service_counts: dict[str, ServiceEntryCounts],
) -> str | list[tuple[str, str]]:
    effective_selected_values = tuple(_effective_service_selection(selected_values))
    if not effective_selected_values:
        totals = ServiceEntryCounts(domains=0, ipv4=0, ipv6=0)
    else:
        totals = ServiceEntryCounts(
            domains=_sum_entry_counts(
                service_counts.get(service_key, ServiceEntryCounts(None, None, None)).domains
                for service_key in effective_selected_values
            ),
            ipv4=_sum_entry_counts(
                service_counts.get(service_key, ServiceEntryCounts(None, None, None)).ipv4
                for service_key in effective_selected_values
            ),
            ipv6=_sum_entry_counts(
                service_counts.get(service_key, ServiceEntryCounts(None, None, None)).ipv6
                for service_key in effective_selected_values
            ),
        )

    domain_count = _format_entry_count(totals.domains)
    prefix = f"{'Итого выбрано':<{SERVICE_SELECTION_SERVICE_WIDTH}} | "
    suffix = (
        f" | {_format_entry_count(totals.ipv4):>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {_format_entry_count(totals.ipv6):>{SERVICE_SELECTION_COUNT_WIDTH}}"
    )
    if totals.domains is not None and totals.domains > KEENETIC_DOMAIN_SELECTION_LIMIT:
        return [
            ("class:footer", prefix),
            ("fg:#ff5f5f bold", f"{domain_count:>{SERVICE_SELECTION_COUNT_WIDTH}}"),
            ("class:footer", suffix),
        ]

    return (
        f"{'Итого выбрано':<{SERVICE_SELECTION_SERVICE_WIDTH}} "
        f"| {domain_count:>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {_format_entry_count(totals.ipv4):>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {_format_entry_count(totals.ipv6):>{SERVICE_SELECTION_COUNT_WIDTH}}"
    )


def _enabled_service_selection_groups(
    enabled_service_keys: set[str],
) -> dict[str, tuple[str, ...]]:
    return {
        parent: children
        for parent, children in SERVICE_SELECTION_GROUPS.items()
        if parent in enabled_service_keys
        and all(child in enabled_service_keys for child in children)
    }


def _effective_service_selection(selected_values: Iterable[str]) -> set[str]:
    selected = set(selected_values)
    for parent, children in SERVICE_SELECTION_GROUPS.items():
        child_set = set(children)
        if parent in selected:
            selected.difference_update(child_set)
        elif child_set.issubset(selected):
            selected.difference_update(child_set)
            selected.add(parent)
    return selected


def _sum_entry_counts(values: Iterable[int | None]) -> int | None:
    total = 0
    for value in values:
        if value is None:
            return None
        total += value
    return total


def _router_state_label(enabled: bool) -> str:
    if enabled:
        return "[bold green]включён[/bold green]"
    return "[bold yellow]выключен[/bold yellow]"


def _router_state_plain(enabled: bool) -> str:
    return "включён" if enabled else "выключен"


def _router_selection_column_widths(routers: Iterable[RouterConfig]) -> tuple[int, int]:
    router_list = list(routers)
    return (
        max(_display_width(router.id) for router in router_list),
        max(_display_width(router.name) for router in router_list),
    )


def _router_selection_title(
    *,
    router: RouterConfig,
    router_id_width: int,
    router_name_width: int,
) -> str:
    return (
        f"{_pad_display(router.id, width=router_id_width)} | "
        f"{_pad_display(router.name, width=router_name_width)} | "
        f"{_router_state_plain(router.enabled)}"
    )


def _router_selection_header(*, router_id_width: int, router_name_width: int) -> str:
    return f"{'Маршрутизатор':<{router_id_width}} | {'Имя':<{router_name_width}} | Статус"


def _router_toggle_title(
    *,
    router: RouterConfig,
    router_id_width: int,
    router_name_width: int,
) -> str:
    return (
        f"{_pad_display(router.id, width=router_id_width)} | "
        f"{_pad_display(router.name, width=router_name_width)}"
    )


def _router_toggle_header(*, router_id_width: int, router_name_width: int) -> str:
    return f"{'Маршрутизатор':<{router_id_width}} | {'Имя':<{router_name_width}}"


def _router_toggle_summary(*, selected_values: tuple[str, ...], total: int) -> str:
    enabled_count = len(selected_values)
    disabled_count = total - enabled_count
    return f"Будет включено: {enabled_count} | выключено: {disabled_count}"


def _manual_run_selection_summary(*, selected_values: tuple[str, ...]) -> str:
    return f"Будет запущено: {len(selected_values)}"


def _pad_display(value: str, *, width: int) -> str:
    return value + (" " * max(width - _display_width(value), 0))


def _display_width(value: str) -> int:
    width = 0
    for character in value:
        if unicodedata.combining(character):
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
    return width


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


def _format_connected(value: bool | None) -> str:
    if value is None:
        return "[dim]-[/dim]"
    if value:
        return "[green]да[/green]"
    return "[yellow]нет[/yellow]"


def _format_router_diagnostic_error(error_message: str | None) -> Text:
    if not error_message:
        return Text("-", style="dim")
    return Text(_truncate_discovery_error_message(error_message), style="red")


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


def _truncate_discovery_error_message(message: str) -> str:
    normalized_message = " ".join(message.split())
    if len(normalized_message) <= DISCOVERY_ERROR_MESSAGE_LIMIT:
        return normalized_message
    truncated = normalized_message[: DISCOVERY_ERROR_MESSAGE_LIMIT - 1].rstrip()
    return f"{truncated}…"


def _format_run_status(status: RunStatus) -> str:
    if status is RunStatus.SUCCESS:
        return "[green]success[/green]"
    if status is RunStatus.PARTIAL:
        return "[yellow]partial[/yellow]"
    return "[red]failed[/red]"


def _format_dashboard_last_run_at(value: datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M")


def _format_dashboard_router_run_status(status: RouterResultStatus) -> str:
    if status in {RouterResultStatus.UPDATED, RouterResultStatus.NO_CHANGES}:
        return "[green]ok[/green]"
    if status is RouterResultStatus.PARTIAL:
        return "[yellow]partial[/yellow]"
    return "[red]fail[/red]"


def _format_history_finished_at(value) -> str:
    return value.strftime("%d.%m.%Y %H:%M:%S")


def _format_history_page_label(*, total_count: int, page_index: int, page_size: int) -> str:
    if total_count <= 0:
        return "нет записей"
    page_count = ((total_count - 1) // page_size) + 1
    return f"{page_index + 1}/{page_count}"


def _format_history_page_range(
    *,
    total_count: int,
    page_index: int,
    page_size: int,
    page_item_count: int,
) -> str:
    if total_count <= 0 or page_item_count <= 0:
        return "0 из 0"
    start = (page_index * page_size) + 1
    end = start + page_item_count - 1
    return f"{start}-{end} из {total_count}"


def _format_history_router_names(*, config: AppConfig, artifact: RunArtifact) -> str:
    router_names: list[str] = []
    for router_result in artifact.router_results:
        router = _find_router(config=config, router_id=router_result.router_id)
        router_names.append(router.name if router is not None else router_result.router_id)
    return ", ".join(router_names) if router_names else "-"


def _format_artifact_summary(artifact: RunArtifact) -> str:
    changed_services = 0
    failed_services = 0
    for router in artifact.router_results:
        for service in router.service_results:
            if service.error_message is not None:
                failed_services += 1
            if service.added_count > 0 or service.removed_count > 0 or service.route_changed:
                changed_services += 1
    return f"изменено={changed_services} ошибок={failed_services}"


def _format_dns_proxy(value: bool | None) -> str:
    if value is None:
        return "[dim]unknown[/dim]"
    if value:
        return "[green]включён[/green]"
    return "[yellow]выключен[/yellow]"
