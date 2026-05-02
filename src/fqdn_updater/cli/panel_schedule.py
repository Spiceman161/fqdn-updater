from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import ValidationError
from rich.panel import Panel
from rich.table import Table

from fqdn_updater.cli import panel_formatting
from fqdn_updater.cli.panel_prompts import PromptChoice
from fqdn_updater.domain.schedule import RuntimeScheduleConfig, ScheduleWeekday

if TYPE_CHECKING:
    from fqdn_updater.cli.panel import PanelController


SCHEDULE_MENU_HINT_LINES = (
    "Расписание хранится в config и разворачивается только через systemd timer.",
    "Команда установки обновляет host-level unit/timer из сохранённых параметров.",
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


class PanelScheduleFlow:
    """Schedule screens and edit actions for the interactive panel."""

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
    def _management_service(self):
        return self._panel._management_service

    @property
    def _schedule_installer(self):
        return self._panel._schedule_installer

    def _load_config(self):
        return self._panel._load_config()

    def _pause(self):
        return self._panel._pause()

    def _render_summary(self, *, title, rows):
        return self._panel._render_summary(title=title, rows=rows)

    def _display_path(self, path):
        return self._panel._display_path(path)

    def schedule_menu(self) -> None:
        while True:
            schedule = self._load_config().runtime.schedule
            self.render_schedule_screen(schedule=schedule)
            choice = self._prompts.select(
                message="Расписание",
                choices=[
                    _schedule_choice(
                        panel_formatting.ICON_EDIT,
                        "Изменить параметры расписания",
                        "edit",
                    ),
                    _schedule_choice(
                        panel_formatting.ICON_DISABLED,
                        "Выключить расписание",
                        "disable",
                        disabled=("Расписание уже выключено" if not schedule.is_enabled else None),
                    ),
                    _schedule_choice(
                        panel_formatting.ICON_SYSTEMD,
                        "Установить/обновить в systemd",
                        "install",
                    ),
                    _schedule_choice(panel_formatting.ICON_BACK, "Главное меню", "back"),
                ],
                default="edit",
                hint_lines=SCHEDULE_MENU_HINT_LINES,
            )
            if choice in {None, "back"}:
                return
            if choice == "edit":
                self.edit_schedule()
            elif choice == "disable":
                self.disable_schedule()
            elif choice == "install":
                self.install_schedule()

    def edit_schedule(self) -> None:
        existing_schedule = self._load_config().runtime.schedule
        mode = self._prompts.select(
            message="Режим расписания",
            choices=[
                _schedule_choice(panel_formatting.ICON_SCHEDULE, "Каждый день", "daily"),
                _schedule_choice(panel_formatting.ICON_SCHEDULE, "По дням недели", "weekly"),
                _schedule_choice(
                    panel_formatting.ICON_DISABLED, "Выключить расписание", "disabled"
                ),
                _schedule_choice(panel_formatting.ICON_BACK, "Назад", "back"),
            ],
            default=existing_schedule.mode.value,
            hint_lines=SCHEDULE_MENU_HINT_LINES,
        )
        if mode in {None, "back"}:
            return
        if mode == "disabled":
            self.disable_schedule()
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

        self._console.print(f"[green]{panel_formatting.ICON_SAVE} Расписание сохранено.[/green]")
        self._pause()

    def disable_schedule(self) -> None:
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

        self._console.print(
            f"[green]{panel_formatting.ICON_DISABLED} Расписание выключено в config.[/green]"
        )
        self._pause()

    def install_schedule(self) -> None:
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
            f"[green]{panel_formatting.ICON_SYSTEMD} systemd units обновлены:[/green] "
            f"timer_action={result.timer_action}"
        )
        self._console.print(self._display_path(result.service_path))
        self._console.print(self._display_path(result.timer_path))
        self._pause()

    def render_schedule_screen(self, *, schedule: RuntimeScheduleConfig) -> None:
        self._console.clear()
        self._console.print(
            Panel(
                f"[bold]{panel_formatting.ICON_SCHEDULE} Расписание[/bold]",
                border_style="bright_cyan",
                width=panel_formatting.ROOT_PANEL_WIDTH,
            )
        )
        self._console.print(
            Panel(
                _schedule_summary_table(schedule),
                title=panel_formatting._icon_label(panel_formatting.ICON_CONFIG, "Текущий config"),
                border_style="cyan",
                width=panel_formatting.ROOT_PANEL_WIDTH,
            )
        )


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


def _schedule_choice(
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
