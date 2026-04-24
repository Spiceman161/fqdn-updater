from __future__ import annotations

from pathlib import Path

import pytest

from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.infrastructure.systemd_scheduler import (
    SystemCommandFailedError,
    SystemCommandNotFoundError,
    SystemdScheduleInstaller,
)


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: tuple[str, ...]) -> None:
        self.commands.append(command)


class FailingRunner:
    def __init__(self, exc: RuntimeError) -> None:
        self.exc = exc
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: tuple[str, ...]) -> None:
        self.commands.append(command)
        raise self.exc


class PermissionDeniedWriter:
    def write(self, *, path: Path, content: str) -> None:
        del path, content
        raise PermissionError("denied")


def test_systemd_schedule_installer_writes_units_and_starts_new_timer(tmp_path) -> None:
    runner = RecordingRunner()
    installer = SystemdScheduleInstaller(
        systemd_dir=tmp_path / "systemd",
        command_runner=runner,
    )

    result = installer.install(
        config=_config(
            {
                "mode": "daily",
                "times": ["03:15"],
                "timezone": "Europe/Moscow",
            }
        ),
        config_path=tmp_path / "config.json",
    )

    service_text = result.service_path.read_text(encoding="utf-8")
    timer_text = result.timer_path.read_text(encoding="utf-8")
    assert (
        "ExecStart=/usr/bin/docker compose run --rm fqdn-updater "
        "sync --trigger scheduled --config /work/config.json" in service_text
    )
    assert "OnCalendar=*-*-* 03:15:00 Europe/Moscow" in timer_text
    assert runner.commands == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "enable", "fqdn-updater.timer"),
        ("systemctl", "start", "fqdn-updater.timer"),
    ]
    assert result.timer_action == "started"


def test_systemd_schedule_installer_restarts_existing_timer_on_update(tmp_path) -> None:
    systemd_dir = tmp_path / "systemd"
    systemd_dir.mkdir(parents=True)
    (systemd_dir / "fqdn-updater.timer").write_text("old timer", encoding="utf-8")
    runner = RecordingRunner()
    installer = SystemdScheduleInstaller(
        systemd_dir=systemd_dir,
        command_runner=runner,
    )

    result = installer.install(
        config=_config(
            {
                "mode": "weekly",
                "weekdays": ["mon", "fri"],
                "times": ["04:00"],
                "timezone": "Europe/Moscow",
            }
        ),
        config_path=tmp_path / "config.json",
    )

    assert "OnCalendar=Mon *-*-* 04:00:00 Europe/Moscow" in result.timer_path.read_text(
        encoding="utf-8"
    )
    assert runner.commands == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "enable", "fqdn-updater.timer"),
        ("systemctl", "restart", "fqdn-updater.timer"),
    ]
    assert result.timer_action == "restarted"


def test_systemd_schedule_installer_disables_timer_for_disabled_schedule(tmp_path) -> None:
    runner = RecordingRunner()
    installer = SystemdScheduleInstaller(
        systemd_dir=tmp_path / "systemd",
        command_runner=runner,
    )

    result = installer.install(
        config=_config({"mode": "disabled"}),
        config_path=tmp_path / "config.json",
    )

    timer_text = result.timer_path.read_text(encoding="utf-8")
    assert "# Disabled in config" in timer_text
    assert runner.commands == [
        ("systemctl", "daemon-reload"),
        ("systemctl", "stop", "fqdn-updater.timer"),
        ("systemctl", "disable", "fqdn-updater.timer"),
    ]
    assert result.timer_action == "disabled"


def test_systemd_schedule_installer_reports_permission_denied_with_sudo_hint(tmp_path) -> None:
    installer = SystemdScheduleInstaller(
        systemd_dir=tmp_path / "systemd",
        command_runner=RecordingRunner(),
        file_writer=PermissionDeniedWriter(),
    )

    with pytest.raises(
        RuntimeError, match="Re-run with sudo: sudo fqdn-updater schedule install --config"
    ):
        installer.install(
            config=_config(
                {
                    "mode": "daily",
                    "times": ["03:15"],
                    "timezone": "Europe/Moscow",
                }
            ),
            config_path=tmp_path / "config.json",
        )


def test_systemd_schedule_installer_reports_missing_systemctl(tmp_path) -> None:
    installer = SystemdScheduleInstaller(
        systemd_dir=tmp_path / "systemd",
        command_runner=FailingRunner(
            SystemCommandNotFoundError(
                ("systemctl", "daemon-reload"),
                "Command not found: systemctl",
            )
        ),
    )

    with pytest.raises(RuntimeError, match="systemctl is not available"):
        installer.install(
            config=_config(
                {
                    "mode": "daily",
                    "times": ["03:15"],
                    "timezone": "Europe/Moscow",
                }
            ),
            config_path=tmp_path / "config.json",
        )


def test_systemd_schedule_installer_reports_systemctl_permission_denied(tmp_path) -> None:
    installer = SystemdScheduleInstaller(
        systemd_dir=tmp_path / "systemd",
        command_runner=FailingRunner(
            SystemCommandFailedError(
                ("systemctl", "daemon-reload"),
                message="Access denied",
                stderr="Access denied",
                returncode=1,
            )
        ),
    )

    with pytest.raises(
        RuntimeError, match="Re-run with sudo: sudo fqdn-updater schedule install --config"
    ):
        installer.install(
            config=_config(
                {
                    "mode": "daily",
                    "times": ["03:15"],
                    "timezone": "Europe/Moscow",
                }
            ),
            config_path=tmp_path / "config.json",
        )


def _config(schedule: dict[str, object]) -> AppConfig:
    return AppConfig.model_validate(
        {
            "routers": [],
            "services": [],
            "mappings": [],
            "runtime": {
                "schedule": schedule,
            },
        }
    )
