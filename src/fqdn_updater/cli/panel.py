from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from fqdn_updater.application.config_bootstrap import ConfigBootstrapService
from fqdn_updater.application.config_management import ConfigManagementService
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig
from fqdn_updater.infrastructure.config_repository import ConfigRepository
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


class PanelController:
    """Interactive terminal control panel for local config management."""

    def __init__(self, *, config_path: Path, console: Console | None = None) -> None:
        self._config_path = config_path
        self._console = console or Console()
        self._repository = ConfigRepository()
        self._bootstrap_service = ConfigBootstrapService(repository=self._repository)
        self._management_service = ConfigManagementService(repository=self._repository)

    def run(self) -> None:
        self._ensure_config_exists()
        while True:
            config = self._load_config()
            self._render_dashboard(config=config)
            choice = Prompt.ask(
                "  [bold]>[/bold]",
                choices=["0", "1", "2", "3", "4", "5"],
                default="0",
                show_choices=False,
                console=self._console,
            )
            if choice == "0":
                return
            if choice == "1":
                self._router_menu()
            elif choice == "2":
                self._lists_menu()
            elif choice == "3":
                self._runs_menu(config=config)
            elif choice == "4":
                self._config_menu(config=config)
            elif choice == "5":
                self._about_menu()

    def _ensure_config_exists(self) -> None:
        if self._config_path.exists():
            return

        should_create = Confirm.ask(
            f"Config file '{self._config_path}' does not exist. Create scaffold config?",
            default=True,
            console=self._console,
        )
        if not should_create:
            raise RuntimeError(f"Config file does not exist: {self._config_path}")
        self._bootstrap_service.create_default_config(path=self._config_path)
        self._console.print(f"[green]Created scaffold config:[/green] {self._config_path}")

    def _load_config(self) -> AppConfig:
        return self._repository.load(path=self._config_path)

    def _render_dashboard(self, *, config: AppConfig) -> None:
        self._console.clear()
        width = 62
        title = Text("FQDN-updater", style="bold white")
        subtitle = Text("control panel", style="dim")
        header = Text.assemble(title, "  ", subtitle)
        self._console.print(Panel(header, border_style="cyan", width=width))

        summary = Table.grid(padding=(0, 2))
        summary.add_column(style="bold white")
        summary.add_column(style="cyan")
        summary.add_row("Config", str(self._config_path))
        summary.add_row("Secrets", str(self._secrets_env_path(config=config)))
        summary.add_row("Routers", str(len(config.routers)))
        summary.add_row("Mappings", str(len(config.mappings)))
        summary.add_row("Artifacts", config.runtime.artifacts_dir)
        summary.add_row("Logs", config.runtime.logs_dir)
        self._console.print(Panel(summary, border_style="dim", width=width))

        router_table = Table(show_header=True, header_style="bold white", box=None)
        router_table.add_column("Router")
        router_table.add_column("Enabled")
        router_table.add_column("Lists")
        router_table.add_column("RCI")
        for router in config.routers:
            selected_services = sorted(
                mapping.service_key for mapping in config.mappings if mapping.router_id == router.id
            )
            enabled_marker = "[green]●[/green]" if router.enabled else "[yellow]○[/yellow]"
            router_table.add_row(
                router.id,
                enabled_marker,
                ", ".join(selected_services) if selected_services else "[dim]none[/dim]",
                str(router.rci_url),
            )
        if not config.routers:
            router_table.add_row("[dim]none[/dim]", "-", "-", "-")
        self._console.print(Panel(router_table, border_style="dim", width=width))

        menu = "\n".join(
            (
                "[cyan]1[/cyan]) Routers",
                "[cyan]2[/cyan]) Lists",
                "[cyan]3[/cyan]) Runs",
                "[cyan]4[/cyan]) Config",
                "[cyan]5[/cyan]) About",
                "[cyan]0[/cyan]) [dim]Exit[/dim]",
            )
        )
        self._console.print(Panel(menu, border_style="cyan", width=width))

    def _router_menu(self) -> None:
        config = self._load_config()
        router_id = Prompt.ask("Router id", console=self._console).strip()
        existing_router = _find_router(config=config, router_id=router_id)
        if existing_router is not None:
            replace = Confirm.ask(
                f"Router '{router_id}' already exists. Replace router fields and password?",
                default=False,
                console=self._console,
            )
            if not replace:
                return

        name = Prompt.ask(
            "Router name",
            default=existing_router.name if existing_router is not None else router_id,
            console=self._console,
        )
        rci_url = Prompt.ask(
            "KeenDNS RCI URL",
            default=str(existing_router.rci_url) if existing_router is not None else "",
            console=self._console,
        )
        username = Prompt.ask(
            "RCI username",
            default=existing_router.username if existing_router is not None else "api-updater",
            console=self._console,
        )
        timeout_seconds = int(
            Prompt.ask(
                "RCI timeout seconds",
                default=(
                    str(existing_router.timeout_seconds) if existing_router is not None else "10"
                ),
                console=self._console,
            )
        )
        selected_services: set[str] | None = None
        mappings: list[dict[str, Any]] | None = None
        if existing_router is None:
            selected_services = self._prompt_service_selection(
                config=config,
                selected=DEFAULT_SELECTED_SERVICES,
            )
            mappings = self._build_mappings(
                router_id=router_id, selected_services=selected_services
            )

        password = secrets.token_urlsafe(36)
        password_env = password_env_key_for_router_id(router_id)
        self._render_router_save_summary(
            router_id=router_id,
            name=name,
            rci_url=rci_url,
            username=username,
            password_env=password_env,
            selected_services=selected_services,
            replacing=existing_router is not None,
        )
        if not Confirm.ask("Save changes?", default=True, console=self._console):
            return

        SecretEnvFile(path=self._secrets_env_path(config=config)).write_value(
            key=password_env,
            value=password,
        )
        self._management_service.save_router_setup(
            path=self._config_path,
            router_id=router_id,
            name=name,
            rci_url=rci_url,
            username=username,
            password_env=password_env,
            password_file=None,
            enabled=True,
            tags=list(existing_router.tags) if existing_router is not None else [],
            timeout_seconds=timeout_seconds,
            allowed_source_ips=(
                list(existing_router.allowed_source_ips) if existing_router is not None else []
            ),
            replace_mappings=mappings,
        )
        self._console.print("[green]Router saved.[/green]")
        self._console.print("[bold]Generated password, shown once:[/bold]")
        self._console.print(password)
        self._console.print(
            "Create or update the low-privilege Keenetic RCI user with this password."
        )
        Prompt.ask("Press Enter to continue", default="", console=self._console)

    def _lists_menu(self) -> None:
        config = self._load_config()
        router = self._select_router(config=config)
        if router is None:
            return
        selected = self._prompt_service_selection(
            config=config,
            selected={
                mapping.service_key for mapping in config.mappings if mapping.router_id == router.id
            },
        )
        mappings = self._build_mappings(router_id=router.id, selected_services=selected)
        self._console.print(
            f"Selected lists for [bold]{router.id}[/bold]: {', '.join(sorted(selected)) or 'none'}"
        )
        if not Confirm.ask("Save list selection?", default=True, console=self._console):
            return
        self._management_service.replace_router_mappings(
            path=self._config_path,
            router_id=router.id,
            mappings=mappings,
        )
        self._console.print("[green]Lists saved.[/green]")
        Prompt.ask("Press Enter to continue", default="", console=self._console)

    def _runs_menu(self, *, config: AppConfig) -> None:
        self._console.print("[bold]Run commands[/bold]")
        self._console.print(f"Artifacts: {config.runtime.artifacts_dir}")
        self._console.print(f"Logs: {config.runtime.logs_dir}")
        self._console.print(f"Status:  fqdn-updater status --config {self._config_path}")
        self._console.print(f"Preview: fqdn-updater dry-run --config {self._config_path}")
        self._console.print(f"Apply:   fqdn-updater sync --config {self._config_path}")
        Prompt.ask("Press Enter to continue", default="", console=self._console)

    def _config_menu(self, *, config: AppConfig) -> None:
        self._console.print("[green]Config is valid.[/green]")
        self._console.print(
            f"routers={len(config.routers)} services={len(config.services)} "
            f"mappings={len(config.mappings)}"
        )
        self._console.print(f"secrets_env_file={config.runtime.secrets_env_file}")
        Prompt.ask("Press Enter to continue", default="", console=self._console)

    def _about_menu(self) -> None:
        self._console.print("[bold]FQDN-updater panel[/bold]")
        self._console.print("Terminal control panel for Keenetic RCI FQDN list management.")
        self._console.print("Router writes still happen only through dry-run/sync flows.")
        Prompt.ask("Press Enter to continue", default="", console=self._console)

    def _select_router(self, *, config: AppConfig) -> RouterConfig | None:
        if not config.routers:
            self._console.print("[yellow]No routers configured.[/yellow]")
            Prompt.ask("Press Enter to continue", default="", console=self._console)
            return None

        table = Table(show_header=True, header_style="bold white")
        table.add_column("#")
        table.add_column("Router")
        table.add_column("Name")
        for index, router in enumerate(config.routers, start=1):
            table.add_row(str(index), router.id, router.name)
        self._console.print(table)
        choice = Prompt.ask(
            "Router number",
            choices=[str(index) for index in range(1, len(config.routers) + 1)],
            console=self._console,
        )
        return config.routers[int(choice) - 1]

    def _prompt_service_selection(self, *, config: AppConfig, selected: set[str]) -> set[str]:
        service_keys = [service.key for service in config.services if service.enabled]
        normalized_selected = {service for service in selected if service in service_keys}

        while True:
            table = Table(show_header=True, header_style="bold white")
            table.add_column("#")
            table.add_column("Use")
            table.add_column("Service")
            for index, service_key in enumerate(service_keys, start=1):
                marker = (
                    Text("[x]", style="green")
                    if service_key in normalized_selected
                    else Text("[ ]", style="dim")
                )
                table.add_row(str(index), marker, service_key)
            self._console.print(table)
            raw_numbers = Prompt.ask(
                "Toggle service numbers, or press Enter to accept",
                default="",
                console=self._console,
            )
            if raw_numbers.strip():
                for number in _split_numbers(raw_numbers):
                    if number < 1 or number > len(service_keys):
                        raise RuntimeError(f"Service number out of range: {number}")
                    service_key = service_keys[number - 1]
                    if service_key in normalized_selected:
                        normalized_selected.remove(service_key)
                    else:
                        normalized_selected.add(service_key)
                continue
            if Confirm.ask("Accept selected services?", default=True, console=self._console):
                return normalized_selected

    def _build_mappings(
        self, *, router_id: str, selected_services: set[str]
    ) -> list[dict[str, Any]]:
        if not selected_services:
            return []

        default_interface = Prompt.ask(
            "Default Keenetic route interface",
            default="Wireguard0",
            console=self._console,
        )
        google_ai_interface = default_interface
        if "google_ai" in selected_services:
            separate_google_ai = Confirm.ask(
                "Use a separate interface for google_ai?",
                default=False,
                console=self._console,
            )
            if separate_google_ai:
                google_ai_interface = Prompt.ask(
                    "google_ai route interface",
                    default=default_interface,
                    console=self._console,
                )

        mappings: list[dict[str, Any]] = []
        for service_key in sorted(selected_services):
            route_target_value = (
                google_ai_interface if service_key == "google_ai" else default_interface
            )
            mappings.append(
                {
                    "router_id": router_id,
                    "service_key": service_key,
                    "object_group_name": f"fqdn-{service_key}",
                    "route_target_type": "interface",
                    "route_target_value": route_target_value,
                    "route_interface": None,
                    "exclusive": True,
                    "auto": True,
                    "managed": True,
                }
            )
        return mappings

    def _render_router_save_summary(
        self,
        *,
        router_id: str,
        name: str,
        rci_url: str,
        username: str,
        password_env: str,
        selected_services: set[str] | None,
        replacing: bool,
    ) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold white")
        table.add_column(style="cyan")
        table.add_row("Mode", "replace router only" if replacing else "add router")
        table.add_row("Router", router_id)
        table.add_row("Name", name)
        table.add_row("RCI URL", rci_url)
        table.add_row("Username", username)
        table.add_row("Password env", password_env)
        if selected_services is not None:
            table.add_row("Lists", ", ".join(sorted(selected_services)) or "none")
        self._console.print(Panel(table, title="Save summary", border_style="cyan"))

    def _secrets_env_path(self, *, config: AppConfig) -> Path:
        path = Path(config.runtime.secrets_env_file)
        if path.is_absolute():
            return path
        return self._config_path.parent / path


def _find_router(*, config: AppConfig, router_id: str) -> RouterConfig | None:
    for router in config.routers:
        if router.id == router_id:
            return router
    return None


def _split_numbers(value: str) -> list[int]:
    numbers: list[int] = []
    for part in value.replace(",", " ").split():
        numbers.append(int(part))
    return numbers
