from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
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
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig
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


class PanelController:
    """Interactive terminal control panel for local config management."""

    def __init__(self, *, config_path: Path, console: Console | None = None) -> None:
        self._config_path = config_path
        self._console = console or Console()
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
        menu = "\n".join(
            (
                "[cyan]1[/cyan]) Add or replace router",
                "[cyan]2[/cyan]) Rotate router password",
                "[cyan]0[/cyan]) [dim]Back[/dim]",
            )
        )
        self._console.print(Panel(menu, title="Routers", border_style="cyan"))
        choice = Prompt.ask(
            "  [bold]>[/bold]",
            default="1",
            show_choices=False,
            console=self._console,
        )
        normalized_choice = choice.strip()
        if normalized_choice in {"", "1"}:
            self._add_or_replace_router(initial_router_id=None)
        elif normalized_choice == "2":
            self._rotate_router_password()
        elif normalized_choice != "0":
            self._add_or_replace_router(initial_router_id=normalized_choice)

    def _add_or_replace_router(self, *, initial_router_id: str | None) -> None:
        config = self._load_config()
        router_id = initial_router_id or Prompt.ask("Router id", console=self._console).strip()
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
        rci_url = normalize_rci_url_input(rci_url)
        username = Prompt.ask(
            "RCI username",
            default=existing_router.username if existing_router is not None else "api_updater",
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
        password_env = password_env_key_for_router_id(router_id)
        password_file = None
        _ensure_password_env_available(
            config=config,
            router_id=router_id,
            password_env=password_env,
        )
        selected_services: set[str] | None = None
        mappings: list[dict[str, Any]] | None = None
        if existing_router is None:
            try:
                draft_router = RouterConfig.model_validate(
                    {
                        "id": router_id,
                        "name": name,
                        "rci_url": rci_url,
                        "username": username,
                        "password_env": password_env,
                        "password_file": password_file,
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
                Prompt.ask("Press Enter to continue", default="", console=self._console)
                return
            selected_services = self._prompt_service_selection(
                config=config,
                selected=DEFAULT_SELECTED_SERVICES,
            )
            route_target_candidates = (
                self._discover_route_targets(
                    config=config,
                    router=draft_router,
                    missing_secret_message=(
                        "WireGuard interface discovery skipped: save this router, "
                        "apply the generated password on Keenetic, then reopen Lists "
                        "for this router."
                    ),
                )
                if selected_services
                else ()
            )
            mappings = self._build_mappings(
                router_id=router_id,
                selected_services=selected_services,
                route_target_candidates=route_target_candidates,
            )

        password = self._password_generator.generate()
        self._render_router_save_summary(
            router_id=router_id,
            name=name,
            rci_url=rci_url,
            username=username,
            password_env=password_env,
            password_file=password_file,
            selected_services=selected_services,
            replacing=existing_router is not None,
        )
        if not Confirm.ask("Save changes?", default=True, console=self._console):
            return

        self._management_service.save_router_setup(
            path=self._config_path,
            router_id=router_id,
            name=name,
            rci_url=rci_url,
            username=username,
            password_env=password_env,
            password_file=password_file,
            enabled=True,
            tags=list(existing_router.tags) if existing_router is not None else [],
            timeout_seconds=timeout_seconds,
            allowed_source_ips=(
                list(existing_router.allowed_source_ips) if existing_router is not None else []
            ),
            replace_mappings=mappings,
        )
        try:
            SecretEnvFile(path=self._secrets_env_path(config=config)).write_value(
                key=password_env,
                value=password,
            )
        except Exception:
            self._repository.overwrite(path=self._config_path, config=config)
            raise
        self._console.print("[green]Router saved.[/green]")
        self._show_generated_password(password=password)
        Prompt.ask("Press Enter to continue", default="", console=self._console)

    def _rotate_router_password(self) -> None:
        config = self._load_config()
        router = self._select_router(config=config)
        if router is None:
            return

        password_env = router.password_env or password_env_key_for_router_id(router.id)
        password = self._password_generator.generate()
        _ensure_password_env_available(
            config=config,
            router_id=router.id,
            password_env=password_env,
        )
        self._render_password_rotation_summary(router=router, password_env=password_env)
        if not Confirm.ask("Rotate router password?", default=False, console=self._console):
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

        self._console.print("[green]Router password rotated.[/green]")
        self._show_generated_password(password=password)
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
        route_target_candidates = (
            self._discover_route_targets(
                config=config,
                router=router,
                missing_secret_message=None,
            )
            if selected
            else ()
        )
        mappings = self._build_mappings(
            router_id=router.id,
            selected_services=selected,
            route_target_candidates=route_target_candidates,
        )
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
        while True:
            config = self._load_config()
            history = self._run_history_service.list_recent(
                config=config,
                config_path=self._config_path,
                limit=8,
            )
            self._render_runs_screen(config=config, history=history)
            choice = Prompt.ask(
                "  [bold]>[/bold]",
                choices=["0", "1", "2"],
                default="0",
                show_choices=False,
                console=self._console,
            )
            if choice == "0":
                return
            if choice == "1":
                self._run_status_diagnostics()
            elif choice == "2":
                self._run_dry_run_preview()

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
        self._console.print("Router writes happen only through confirmed sync flows.")
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
        self,
        *,
        router_id: str,
        selected_services: set[str],
        route_target_candidates: tuple[RouteTargetCandidate, ...],
    ) -> list[dict[str, Any]]:
        if not selected_services:
            return []

        default_interface = self._prompt_route_interface(
            label="Default Keenetic route interface",
            candidates=route_target_candidates,
            default="Wireguard0",
        )
        google_ai_interface = default_interface
        if "google_ai" in selected_services:
            separate_google_ai = Confirm.ask(
                "Use a separate interface for google_ai?",
                default=False,
                console=self._console,
            )
            if separate_google_ai:
                google_ai_interface = self._prompt_route_interface(
                    label="google_ai route interface",
                    candidates=route_target_candidates,
                    default=default_interface,
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
            self._console.print(f"[yellow]WireGuard interface discovery failed:[/yellow] {exc}")
            return ()

        result = self._route_target_discovery_service.discover_wireguard_targets(router=router)
        if result.error_message is not None:
            if missing_secret_message is not None and _is_missing_password_env_error(
                result.error_message
            ):
                self._console.print(f"[yellow]{missing_secret_message}[/yellow]")
                return ()
            self._console.print(
                f"[yellow]WireGuard interface discovery failed:[/yellow] {result.error_message}"
            )
            return ()
        if not result.candidates:
            self._console.print("[yellow]No WireGuard interfaces discovered.[/yellow]")
            return ()
        return result.candidates

    def _render_runs_screen(self, *, config: AppConfig, history: RunHistoryResult) -> None:
        self._console.print(Panel("[bold]Runs[/bold]", border_style="cyan", width=78))
        self._console.print(f"Artifacts: {history.artifacts_dir}")
        self._console.print(f"Logs: {self._resolve_config_relative_path(config.runtime.logs_dir)}")

        table = Table(show_header=True, header_style="bold white", box=None)
        table.add_column("Run")
        table.add_column("Mode")
        table.add_column("Status")
        table.add_column("Finished")
        table.add_column("Routers")
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
            table.add_row("[dim]none[/dim]", "-", "-", "-", "-", "-")
        self._console.print(Panel(table, title="Recent local artifacts", border_style="dim"))

        for warning in history.warnings[:3]:
            self._console.print(
                f"[yellow]Artifact skipped:[/yellow] {warning.path}: {warning.message}"
            )
        if len(history.warnings) > 3:
            omitted_count = len(history.warnings) - 3
            self._console.print(f"[yellow]Artifact warnings omitted:[/yellow] {omitted_count}")

        menu = "\n".join(
            (
                "[cyan]1[/cyan]) Status diagnostics [dim](read-only network check)[/dim]",
                "[cyan]2[/cyan]) Dry-run preview [dim](read-only; writes run artifact/log)[/dim]",
                f"[dim]Manual apply:[/dim] fqdn-updater sync --config {self._config_path}",
                "[cyan]0[/cyan]) [dim]Back[/dim]",
            )
        )
        self._console.print(Panel(menu, border_style="cyan", width=78))

    def _run_status_diagnostics(self) -> None:
        config = self._config_with_resolved_runtime_paths(config=self._load_config())
        try:
            self._load_runtime_secret_env_file(config=config)
            result = self._status_service.check(config=config)
        except RuntimeError as exc:
            self._console.print(f"[red]Status diagnostics failed:[/red] {exc}")
            Prompt.ask("Press Enter to continue", default="", console=self._console)
            return

        self._render_status_result(result=result)
        Prompt.ask("Press Enter to continue", default="", console=self._console)

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
            Prompt.ask("Press Enter to continue", default="", console=self._console)
            return

        self._render_dry_run_result(result=result)
        Prompt.ask("Press Enter to continue", default="", console=self._console)

    def _render_status_result(self, *, result: StatusDiagnosticsResult) -> None:
        table = Table(show_header=True, header_style="bold white")
        table.add_column("Router")
        table.add_column("Status")
        table.add_column("DNS proxy")
        table.add_column("Detail")
        for router in result.router_results:
            table.add_row(
                router.router_id,
                router.status.value,
                _format_dns_proxy(router.dns_proxy_enabled),
                router.error_message or "[dim]-[/dim]",
            )
        if not result.router_results:
            table.add_row("[dim]none[/dim]", "-", "-", "-")
        title = (
            f"Status diagnostics: overall={result.overall_status.value} "
            f"checked={result.checked_router_count}"
        )
        self._console.print(Panel(table, title=title, border_style="cyan"))

    def _render_dry_run_result(self, *, result: DryRunExecutionResult) -> None:
        artifact = result.artifact
        table = Table(show_header=True, header_style="bold white")
        table.add_column("Router")
        table.add_column("Status")
        table.add_column("Services")
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
            table.add_row("[dim]none[/dim]", "-", "-", "-")

        title = (
            f"Dry-run: run_id={artifact.run_id} status={artifact.status.value} "
            f"artifact={result.artifact_path}"
        )
        self._console.print(Panel(table, title=title, border_style="cyan"))

    def _prompt_route_interface(
        self,
        *,
        label: str,
        candidates: tuple[RouteTargetCandidate, ...],
        default: str,
    ) -> str:
        if not candidates:
            return Prompt.ask(label, default=default, console=self._console)

        self._render_route_target_candidates(candidates=candidates)
        choice = Prompt.ask(
            f"{label} number, or manual value",
            default=default,
            console=self._console,
        ).strip()
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(candidates):
                return candidates[index - 1].value
            raise RuntimeError(f"Route interface number out of range: {index}")
        return choice or default

    def _render_route_target_candidates(
        self,
        *,
        candidates: tuple[RouteTargetCandidate, ...],
    ) -> None:
        table = Table(show_header=True, header_style="bold white")
        table.add_column("#")
        table.add_column("Interface")
        table.add_column("Connected")
        table.add_column("Status")
        table.add_column("Detail")
        for index, candidate in enumerate(candidates, start=1):
            table.add_row(
                str(index),
                candidate.display_name or candidate.value,
                _format_connected(candidate.connected),
                candidate.status or "[dim]-[/dim]",
                candidate.detail or "[dim]-[/dim]",
            )
        self._console.print(table)

    def _render_router_save_summary(
        self,
        *,
        router_id: str,
        name: str,
        rci_url: str,
        username: str,
        password_env: str | None,
        password_file: str | None,
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
        table.add_row("Password env", password_env or "[dim]none[/dim]")
        table.add_row("Password file", password_file or "[dim]none[/dim]")
        if selected_services is not None:
            table.add_row("Lists", ", ".join(sorted(selected_services)) or "none")
        self._console.print(Panel(table, title="Save summary", border_style="cyan"))

    def _render_password_rotation_summary(
        self,
        *,
        router: RouterConfig,
        password_env: str,
    ) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold white")
        table.add_column(style="cyan")
        table.add_row("Mode", "rotate password")
        table.add_row("Router", router.id)
        table.add_row("Password env", password_env)
        if router.password_file is not None:
            table.add_row(
                "Password file",
                f"{router.password_file} [yellow](will be cleared)[/yellow]",
            )
        self._console.print(Panel(table, title="Rotation summary", border_style="cyan"))

    def _show_generated_password(self, *, password: str) -> None:
        self._console.print("[bold]Generated password, shown once:[/bold]")
        self._console.print(password)
        self._console.print(
            "Update the low-privilege Keenetic RCI user with this password now. "
            "The panel will not show it again."
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
                "secrets_env_file": str(self._secrets_env_path(config=config)),
            }
        )
        return config.model_copy(update={"runtime": runtime})

    def _resolve_config_relative_path(self, configured_path: str) -> Path:
        path = Path(configured_path)
        if path.is_absolute():
            return path
        return self._config_path.parent / path


def _find_router(*, config: AppConfig, router_id: str) -> RouterConfig | None:
    for router in config.routers:
        if router.id == router_id:
            return router
    return None


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


def _split_numbers(value: str) -> list[int]:
    numbers: list[int] = []
    for part in value.replace(",", " ").split():
        numbers.append(int(part))
    return numbers


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
