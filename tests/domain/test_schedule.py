from __future__ import annotations

import pytest
from pydantic import ValidationError

from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.schedule import RuntimeScheduleConfig, render_systemd_on_calendar


def test_runtime_defaults_include_state_dir_and_disabled_schedule() -> None:
    config = AppConfig.model_validate(
        {
            "routers": [],
            "services": [],
            "mappings": [],
        }
    )

    assert config.runtime.state_dir == "data/state"
    assert config.runtime.schedule.mode.value == "disabled"
    assert config.runtime.schedule.timezone == "UTC"
    assert config.runtime.schedule.systemd.unit_name == "fqdn-updater"
    assert config.runtime.schedule.systemd.deployment_root == "/opt/fqdn-updater"
    assert config.runtime.schedule.systemd.compose_service == "fqdn-updater"


def test_daily_schedule_accepts_multiple_times_and_normalizes_order() -> None:
    schedule = RuntimeScheduleConfig(
        mode="daily",
        times=["12:00", "03:15"],
        timezone="Europe/Moscow",
    )

    assert schedule.times == ["03:15", "12:00"]
    assert schedule.weekdays == []


def test_weekly_schedule_accepts_multiple_days_and_defaults_systemd_parameters() -> None:
    schedule = RuntimeScheduleConfig(
        mode="weekly",
        weekdays=["fri", "mon"],
        times=["04:00"],
        timezone="Europe/Moscow",
    )

    assert [day.value for day in schedule.weekdays] == ["mon", "fri"]
    assert schedule.systemd.unit_name == "fqdn-updater"
    assert schedule.systemd.deployment_root == "/opt/fqdn-updater"
    assert schedule.systemd.compose_service == "fqdn-updater"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"mode": "daily", "times": [], "timezone": "UTC"},
            "daily schedule requires at least one time value",
        ),
        (
            {
                "mode": "daily",
                "times": ["03:15"],
                "weekdays": ["mon"],
                "timezone": "UTC",
            },
            "daily schedule must not define weekdays",
        ),
        (
            {"mode": "weekly", "times": ["03:15"], "timezone": "UTC"},
            "weekly schedule requires at least one weekday",
        ),
        (
            {"mode": "disabled", "times": ["03:15"], "timezone": "UTC"},
            "disabled schedule must not define times",
        ),
        (
            {"mode": "daily", "times": ["24:00"], "timezone": "UTC"},
            "must use HH:MM in 24-hour format",
        ),
        (
            {"mode": "daily", "times": ["03:15"], "timezone": "Mars/Olympus"},
            "is not a valid IANA timezone",
        ),
        (
            {
                "mode": "weekly",
                "times": ["03:15"],
                "weekdays": ["workday"],
                "timezone": "UTC",
            },
            "must be one of mon, tue, wed, thu, fri, sat, sun",
        ),
    ],
)
def test_schedule_validation_rejects_invalid_payloads(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        RuntimeScheduleConfig(**payload)


def test_render_systemd_on_calendar_for_one_daily_time() -> None:
    schedule = RuntimeScheduleConfig(
        mode="daily",
        times=["03:15"],
        timezone="Europe/Moscow",
    )

    assert render_systemd_on_calendar(schedule) == ("*-*-* 03:15:00 Europe/Moscow",)


def test_render_systemd_on_calendar_for_multiple_daily_times() -> None:
    schedule = RuntimeScheduleConfig(
        mode="daily",
        times=["03:15", "12:00"],
        timezone="Europe/Moscow",
    )

    assert render_systemd_on_calendar(schedule) == (
        "*-*-* 03:15:00 Europe/Moscow",
        "*-*-* 12:00:00 Europe/Moscow",
    )


def test_render_systemd_on_calendar_for_weekly_schedule() -> None:
    schedule = RuntimeScheduleConfig(
        mode="weekly",
        weekdays=["mon", "fri"],
        times=["04:00"],
        timezone="Europe/Moscow",
    )

    assert render_systemd_on_calendar(schedule) == (
        "Mon *-*-* 04:00:00 Europe/Moscow",
        "Fri *-*-* 04:00:00 Europe/Moscow",
    )
