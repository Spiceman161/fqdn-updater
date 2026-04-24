from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.schedule import RuntimeScheduleConfig, render_systemd_on_calendar

_DISABLED_TIMER_PLACEHOLDER = "2099-01-01 00:00:00 UTC"


@dataclass(frozen=True)
class RenderedSystemdUnits:
    service: str
    timer: str


@dataclass(frozen=True)
class SystemdScheduleInstallResult:
    service_path: Path
    timer_path: Path
    timer_action: str


class SystemCommandRunner(Protocol):
    def run(self, command: tuple[str, ...]) -> None:
        """Run a system command or raise a RuntimeError."""


class UnitFileWriter(Protocol):
    def write(self, *, path: Path, content: str) -> None:
        """Persist a systemd unit file."""


class SystemCommandNotFoundError(RuntimeError):
    def __init__(self, command: tuple[str, ...], message: str) -> None:
        super().__init__(message)
        self.command = command


class SystemCommandFailedError(RuntimeError):
    def __init__(
        self,
        command: tuple[str, ...],
        *,
        message: str,
        stderr: str,
        returncode: int,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.stderr = stderr
        self.returncode = returncode


class SubprocessSystemCommandRunner:
    def run(self, command: tuple[str, ...]) -> None:
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError as exc:
            raise SystemCommandNotFoundError(
                command,
                f"Command not found: {command[0]}",
            ) from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or exc.stdout or "").strip()
            message = stderr or (
                f"Command failed with exit code {exc.returncode}: {' '.join(command)}"
            )
            raise SystemCommandFailedError(
                command,
                message=message,
                stderr=stderr,
                returncode=exc.returncode,
            ) from exc


class PathUnitFileWriter:
    def write(self, *, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class SystemdScheduleInstaller:
    def __init__(
        self,
        *,
        systemd_dir: Path = Path("/etc/systemd/system"),
        command_runner: SystemCommandRunner | None = None,
        file_writer: UnitFileWriter | None = None,
        cli_program: str = "fqdn-updater",
    ) -> None:
        self._systemd_dir = systemd_dir
        self._command_runner = command_runner or SubprocessSystemCommandRunner()
        self._file_writer = file_writer or PathUnitFileWriter()
        self._cli_program = cli_program

    def render_units(self, *, schedule: RuntimeScheduleConfig) -> RenderedSystemdUnits:
        return RenderedSystemdUnits(
            service=build_systemd_service_unit(schedule=schedule),
            timer=build_systemd_timer_unit(schedule=schedule),
        )

    def install(
        self,
        *,
        config: AppConfig,
        config_path: Path,
    ) -> SystemdScheduleInstallResult:
        schedule = config.runtime.schedule
        unit_name = schedule.systemd.unit_name
        service_path = self._systemd_dir / f"{unit_name}.service"
        timer_path = self._systemd_dir / f"{unit_name}.timer"
        timer_previously_present = timer_path.exists()
        rendered_units = self.render_units(schedule=schedule)

        try:
            self._file_writer.write(path=service_path, content=rendered_units.service)
            self._file_writer.write(path=timer_path, content=rendered_units.timer)
            self._command_runner.run(("systemctl", "daemon-reload"))
            timer_action = self._sync_timer_state(
                schedule=schedule,
                timer_previously_present=timer_previously_present,
            )
        except PermissionError as exc:
            raise RuntimeError(self._permission_denied_message(config_path=config_path)) from exc
        except OSError as exc:
            if exc.errno is not None and exc.errno in {1, 13}:
                raise RuntimeError(
                    self._permission_denied_message(config_path=config_path)
                ) from exc
            raise RuntimeError(
                f"Failed to write systemd unit files in {self._systemd_dir}: {exc}"
            ) from exc
        except SystemCommandNotFoundError as exc:
            raise RuntimeError(
                self._missing_systemctl_message(config_path=config_path, detail=str(exc))
            ) from exc
        except SystemCommandFailedError as exc:
            if _looks_like_permission_error(exc.stderr or str(exc)):
                raise RuntimeError(
                    self._permission_denied_message(config_path=config_path)
                ) from exc
            raise RuntimeError(
                f"systemctl command failed while installing schedule: {exc}"
            ) from exc

        return SystemdScheduleInstallResult(
            service_path=service_path,
            timer_path=timer_path,
            timer_action=timer_action,
        )

    def _sync_timer_state(
        self,
        *,
        schedule: RuntimeScheduleConfig,
        timer_previously_present: bool,
    ) -> str:
        timer_unit_name = schedule.systemd.timer_unit_name
        if not schedule.is_enabled:
            self._command_runner.run(("systemctl", "stop", timer_unit_name))
            self._command_runner.run(("systemctl", "disable", timer_unit_name))
            return "disabled"

        self._command_runner.run(("systemctl", "enable", timer_unit_name))
        if timer_previously_present:
            self._command_runner.run(("systemctl", "restart", timer_unit_name))
            return "restarted"

        self._command_runner.run(("systemctl", "start", timer_unit_name))
        return "started"

    def _permission_denied_message(self, *, config_path: Path) -> str:
        return (
            "Permission denied while installing systemd units. "
            f"Re-run with sudo: {self._sudo_rerun_command(config_path=config_path)}"
        )

    def _missing_systemctl_message(self, *, config_path: Path, detail: str) -> str:
        return (
            f"systemctl is not available: {detail}. "
            f"Run this on a systemd host or re-run with sudo: "
            f"{self._sudo_rerun_command(config_path=config_path)}"
        )

    def _sudo_rerun_command(self, *, config_path: Path) -> str:
        return f"sudo {self._cli_program} schedule install --config {shlex.quote(str(config_path))}"


def build_systemd_service_unit(*, schedule: RuntimeScheduleConfig) -> str:
    lines = [
        "[Unit]",
        "Description=FQDN-updater one-shot sync",
        "Requires=docker.service network-online.target",
        "After=docker.service network-online.target",
        "",
        "[Service]",
        "Type=oneshot",
        f"WorkingDirectory={schedule.systemd.deployment_root}",
        "ExecStart=/usr/bin/docker compose run --rm "
        f"{schedule.systemd.compose_service} "
        "sync --trigger scheduled --config /work/config.json",
        "",
    ]
    return "\n".join(lines)


def build_systemd_timer_unit(*, schedule: RuntimeScheduleConfig) -> str:
    on_calendar_lines = render_systemd_on_calendar(schedule)
    lines = [
        "[Unit]",
        "Description=Run FQDN-updater sync on a schedule",
        "",
        "[Timer]",
    ]
    if on_calendar_lines:
        lines.extend(f"OnCalendar={line}" for line in on_calendar_lines)
    else:
        lines.append("# Disabled in config; installer keeps this timer stopped and disabled.")
        lines.append(f"OnCalendar={_DISABLED_TIMER_PLACEHOLDER}")
    lines.extend(
        [
            "Persistent=true",
            f"Unit={schedule.systemd.service_unit_name}",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )
    return "\n".join(lines)


def _looks_like_permission_error(message: str) -> bool:
    normalized_message = message.lower()
    return "permission denied" in normalized_message or "access denied" in normalized_message
