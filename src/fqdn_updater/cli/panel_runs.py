from __future__ import annotations

from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from fqdn_updater.application.dry_run_orchestration import DryRunExecutionResult
from fqdn_updater.application.run_history import RecentRun, RunHistoryResult
from fqdn_updater.application.sync_orchestration import SyncExecutionResult
from fqdn_updater.cli.panel_formatting import (
    _find_router,
    _format_dns_proxy,
    _format_router_diagnostic_error,
)
from fqdn_updater.cli.panel_prompts import PromptChoice
from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.run_artifact import (
    FailureCategory,
    RouterRunResult,
    RunArtifact,
    RunStatus,
)
from fqdn_updater.domain.status_diagnostics import StatusDiagnosticsResult

if TYPE_CHECKING:
    from fqdn_updater.cli.panel import PanelController


class PanelRunsFlow:
    """Run history and execution result screens for the interactive panel."""

    def __init__(self, *, panel: PanelController) -> None:
        self._panel = panel

    @property
    def _config_path(self):
        return self._panel._config_path

    @property
    def _console(self):
        return self._panel._console

    @property
    def _prompts(self):
        return self._panel._prompts

    @property
    def _run_history_service(self):
        return self._panel._run_history_service

    def _load_config(self):
        return self._panel._load_config()

    def _display_path(self, path):
        return self._panel._display_path(path)

    def _log_cat_command(self, path):
        return self._panel._log_cat_command(path)

    def runs_menu(self) -> None:
        page_index = 0
        page_size = 10
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
            displayed_runs = tuple(reversed(history.runs))
            if not displayed_runs:
                self.render_runs_screen(
                    config=config,
                    history=history,
                    page_index=page_index,
                    page_size=page_size,
                )
                choice = self._prompts.select(
                    message="Журнал",
                    choices=[PromptChoice("Главное меню", "back")],
                    default="back",
                )
                if choice in {None, "back"}:
                    return
                continue

            choices = [
                PromptChoice(
                    _format_history_run_choice_title(config=config, run=run),
                    f"run:{index}",
                )
                for index, run in enumerate(displayed_runs)
            ]
            choice = self._prompts.history_select(
                message="Журнал",
                choices=choices,
                default="run:0",
                page_label=_format_history_page_text(
                    total_count=history.total_count,
                    page_index=page_index,
                    page_size=page_size,
                ),
                has_previous_page=has_previous_page,
                has_next_page=has_next_page,
            )
            if choice in {None, "back"}:
                return
            if choice == "prev-page":
                page_index -= 1
                continue
            if choice == "next-page":
                page_index += 1
                continue
            if choice.startswith("run:"):
                run_index = int(choice.removeprefix("run:"))
                if 0 <= run_index < len(displayed_runs):
                    self.render_run_details_screen(
                        config=config,
                        run=displayed_runs[run_index],
                    )
                    self._prompts.select(
                        message="Запись журнала",
                        choices=[PromptChoice("Назад к журналу", "back")],
                        default="back",
                    )
                continue

    def render_runs_screen(
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
            )
        )
        self._console.print(
            Text(
                _format_history_page_text(
                    total_count=history.total_count,
                    page_index=page_index,
                    page_size=page_size,
                ),
                style="bright_cyan",
            )
        )
        self._console.print()

        if history.warnings:
            self._console.print(f"[yellow]Пропущено записей: {len(history.warnings)}[/yellow]")

        table = Table(show_header=True, header_style="bold white", box=None, expand=True)
        table.add_column("Режим", no_wrap=True)
        table.add_column("Запуск", no_wrap=True)
        table.add_column("Статус", no_wrap=True)
        table.add_column("Завершён", no_wrap=True)
        table.add_column("Роутеры", overflow="fold")
        table.add_column("Итог", overflow="fold")
        for run in reversed(history.runs):
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
                title="Записи журнала",
                border_style="bright_black",
            )
        )

        if history.warnings:
            warning_table = Table(
                show_header=True,
                header_style="bold white",
                box=None,
                expand=True,
            )
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
                    title="Пропущенные записи",
                    border_style="yellow",
                )
            )

    def render_run_details_screen(self, *, config: AppConfig, run: RecentRun) -> None:
        artifact = run.artifact
        self._console.clear()
        self._console.print(
            Panel(
                f"[bold]Запись журнала[/bold] {run.path.name}",
                border_style="bright_cyan",
            )
        )

        summary = Table.grid(padding=(0, 2), expand=True)
        summary.add_column(style="bold white")
        summary.add_column(style="bright_cyan")
        summary.add_row("Файл записи", self._display_path(run.path))
        summary.add_row("Полный лог", self._display_path(artifact.log_path))
        summary.add_row("Run ID", artifact.run_id)
        summary.add_row("Режим", artifact.mode.value)
        summary.add_row("Запуск", artifact.trigger.value)
        summary.add_row("Статус", _format_run_status(artifact.status))
        summary.add_row("Начало", _format_history_finished_at(artifact.started_at))
        summary.add_row("Завершён", _format_history_finished_at(artifact.finished_at))
        summary.add_row("Итог", _format_artifact_summary(artifact))
        self._console.print(
            Panel(
                summary,
                title="Итог прогона",
                border_style="cyan",
            )
        )

        router_table = Table(show_header=True, header_style="bold white", box=None, expand=True)
        router_table.add_column("Роутер")
        router_table.add_column("Статус", no_wrap=True)
        router_table.add_column("Сервисов", no_wrap=True)
        router_table.add_column("Итог")
        for router_result in artifact.router_results:
            router = _find_router(config=config, router_id=router_result.router_id)
            router_name = router.name if router is not None else router_result.router_id
            router_table.add_row(
                router_name,
                router_result.status.value,
                str(len(router_result.service_results)),
                _format_router_result_summary(router_result),
            )
        if not artifact.router_results:
            router_table.add_row("[dim]нет[/dim]", "-", "-", "-")
        self._console.print(
            Panel(
                router_table,
                title="Маршрутизаторы",
                border_style="bright_black",
            )
        )

        errors = _run_error_rows(config=config, artifact=artifact)
        if errors:
            error_table = Table(show_header=True, header_style="bold white", box=None, expand=True)
            error_table.add_column("Источник")
            error_table.add_column("Класс", no_wrap=True)
            error_table.add_column("Шаг", no_wrap=True)
            error_table.add_column("Ошибка")
            for source, category, step, message in errors:
                error_table.add_row(source, category, step, message)
            self._console.print(
                Panel(
                    error_table,
                    title="Ошибки",
                    border_style="red",
                )
            )
        else:
            self._console.print(
                Panel(
                    "[green]Ошибок в записи нет.[/green]",
                    title="Ошибки",
                    border_style="green",
                )
            )

        self._console.print(
            Panel(
                (
                    "Чтобы посмотреть полный лог этой записи, откройте новое окно терминала "
                    f"и выполните: [bold]{self._log_cat_command(artifact.log_path)}[/bold]"
                ),
                title="Подсказка",
                border_style="yellow",
            )
        )

    def render_status_result(self, *, result: StatusDiagnosticsResult) -> None:
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

    def render_dry_run_result(self, *, result: DryRunExecutionResult) -> None:
        artifact = result.artifact
        title = (
            f"Dry-run: run_id={artifact.run_id} status={artifact.status.value} "
            f"artifact={result.artifact_path}"
        )
        self._console.print(
            Panel(_run_result_table(artifact=artifact), title=title, border_style="bright_cyan")
        )

    def render_sync_result(self, *, result: SyncExecutionResult) -> None:
        artifact = result.artifact
        title = (
            f"Sync: run_id={artifact.run_id} status={artifact.status.value} "
            f"artifact={result.artifact_path}"
        )
        self._console.print(
            Panel(_run_result_table(artifact=artifact), title=title, border_style="bright_cyan")
        )


def _run_result_table(*, artifact: RunArtifact) -> Table:
    table = Table(show_header=True, header_style="bold white")
    table.add_column("Маршрутизатор")
    table.add_column("Статус")
    table.add_column("Сервисов")
    table.add_column("Итог")
    for router in artifact.router_results:
        table.add_row(
            router.router_id,
            router.status.value,
            str(len(router.service_results)),
            _format_router_result_summary(router),
        )
    if not artifact.router_results:
        table.add_row("[dim]нет[/dim]", "-", "-", "-")
    return table


def _format_run_status(status: RunStatus) -> str:
    if status is RunStatus.SUCCESS:
        return "[green]success[/green]"
    if status is RunStatus.PARTIAL:
        return "[yellow]partial[/yellow]"
    return "[red]failed[/red]"


def _format_history_finished_at(value) -> str:
    return value.strftime("%d.%m.%Y %H:%M:%S")


def _format_history_page_text(*, total_count: int, page_index: int, page_size: int) -> str:
    if total_count <= 0:
        return "Страница 0 из 0"
    page_count = ((total_count - 1) // page_size) + 1
    return f"Страница {page_index + 1} из {page_count}"


def _format_history_router_names(*, config: AppConfig, artifact: RunArtifact) -> str:
    router_names: list[str] = []
    for router_result in artifact.router_results:
        router = _find_router(config=config, router_id=router_result.router_id)
        router_names.append(router.name if router is not None else router_result.router_id)
    return ", ".join(router_names) if router_names else "-"


def _format_history_run_choice_title(*, config: AppConfig, run: RecentRun) -> str:
    artifact = run.artifact
    return (
        f"{artifact.mode.value:<8}  "
        f"{artifact.trigger.value:<9}  "
        f"{artifact.status.value:<7}  "
        f"{_format_history_finished_at(artifact.finished_at)}  "
        f"{_format_history_router_names(config=config, artifact=artifact)}  "
        f"{_format_artifact_summary(artifact)}"
    )


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


def _format_router_result_summary(router: RouterRunResult) -> str:
    changed_services = 0
    failed_services = 0
    for service in router.service_results:
        if service.error_message is not None:
            failed_services += 1
        if service.added_count > 0 or service.removed_count > 0 or service.route_changed:
            changed_services += 1
    return f"изменено={changed_services} ошибок={failed_services}"


def _run_error_rows(
    *,
    config: AppConfig,
    artifact: RunArtifact,
) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for router_result in artifact.router_results:
        router = _find_router(config=config, router_id=router_result.router_id)
        router_name = router.name if router is not None else router_result.router_id
        if router_result.error_message is not None:
            message = router_result.error_message
            if _is_grouped_router_transport_failure(router_result):
                affected_count = sum(
                    service_result.error_message is not None
                    and service_result.failure_detail == router_result.failure_detail
                    for service_result in router_result.service_results
                )
                message = f"{message} (затронуто сервисов: {affected_count})"
            rows.append(
                (
                    router_name,
                    _format_failure_category(router_result.failure_detail),
                    _format_failure_step(router_result.failure_detail),
                    message,
                )
            )
        for service_result in router_result.service_results:
            if service_result.error_message is None:
                continue
            if (
                _is_grouped_router_transport_failure(router_result)
                and service_result.failure_detail == router_result.failure_detail
            ):
                continue
            rows.append(
                (
                    f"{router_name} / {service_result.service_key}",
                    _format_failure_category(service_result.failure_detail),
                    _format_failure_step(service_result.failure_detail),
                    service_result.error_message,
                )
            )
    return rows


def _is_grouped_router_transport_failure(router_result: RouterRunResult) -> bool:
    failure_detail = router_result.failure_detail
    return failure_detail is not None and failure_detail.category is not None


def _format_failure_category(failure_detail) -> str:
    if failure_detail is None or failure_detail.category is None:
        return "-"
    category = failure_detail.category
    if isinstance(category, FailureCategory):
        return category.value
    return str(category)


def _format_failure_step(failure_detail) -> str:
    if failure_detail is None:
        return "-"
    return failure_detail.step.value
