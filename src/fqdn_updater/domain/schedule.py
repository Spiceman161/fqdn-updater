from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_WEEKDAY_ORDER = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}
_SYSTEMD_WEEKDAY_NAMES = {
    "mon": "Mon",
    "tue": "Tue",
    "wed": "Wed",
    "thu": "Thu",
    "fri": "Fri",
    "sat": "Sat",
    "sun": "Sun",
}


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


class ScheduleMode(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    DISABLED = "disabled"


class ScheduleWeekday(StrEnum):
    MON = "mon"
    TUE = "tue"
    WED = "wed"
    THU = "thu"
    FRI = "fri"
    SAT = "sat"
    SUN = "sun"


class SystemdScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unit_name: str = "fqdn-updater"
    deployment_root: str = "/opt/fqdn-updater"
    compose_service: str = "fqdn-updater"

    @field_validator("unit_name", "deployment_root", "compose_service", mode="before")
    @classmethod
    def _validate_text_fields(cls, value: str) -> str:
        return _require_non_blank(str(value), "systemd field")

    @property
    def service_unit_name(self) -> str:
        return f"{self.unit_name}.service"

    @property
    def timer_unit_name(self) -> str:
        return f"{self.unit_name}.timer"

    @property
    def deployment_root_path(self) -> Path:
        return Path(self.deployment_root)


class RuntimeScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ScheduleMode = ScheduleMode.DISABLED
    times: list[str] = Field(default_factory=list)
    weekdays: list[ScheduleWeekday] = Field(default_factory=list)
    timezone: str = "UTC"
    systemd: SystemdScheduleConfig = Field(default_factory=SystemdScheduleConfig)

    @field_validator("times", mode="before")
    @classmethod
    def _validate_times(cls, value: object) -> list[str]:
        raw_values = list(value or [])
        seen: set[str] = set()
        normalized_values: list[str] = []
        for raw_value in raw_values:
            normalized_value = _require_non_blank(str(raw_value), "times item")
            if not _TIME_PATTERN.fullmatch(normalized_value):
                raise ValueError(
                    f"times item '{normalized_value}' must use HH:MM in 24-hour format"
                )
            if normalized_value in seen:
                raise ValueError(f"times contains duplicate value '{normalized_value}'")
            seen.add(normalized_value)
            normalized_values.append(normalized_value)
        return sorted(normalized_values)

    @field_validator("weekdays", mode="before")
    @classmethod
    def _validate_weekdays(cls, value: object) -> list[ScheduleWeekday]:
        raw_values = list(value or [])
        seen: set[ScheduleWeekday] = set()
        normalized_values: list[ScheduleWeekday] = []
        for raw_value in raw_values:
            candidate = str(raw_value).strip().lower()
            try:
                normalized_value = ScheduleWeekday(candidate)
            except ValueError as exc:
                raise ValueError(
                    f"weekdays item '{candidate}' must be one of mon, tue, wed, thu, fri, sat, sun"
                ) from exc
            if normalized_value in seen:
                raise ValueError(f"weekdays contains duplicate value '{normalized_value.value}'")
            seen.add(normalized_value)
            normalized_values.append(normalized_value)
        return sorted(normalized_values, key=lambda item: _WEEKDAY_ORDER[item.value])

    @field_validator("timezone", mode="before")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        normalized_value = _require_non_blank(str(value), "timezone")
        try:
            ZoneInfo(normalized_value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"timezone '{normalized_value}' is not a valid IANA timezone") from exc
        return normalized_value

    @model_validator(mode="after")
    def _validate_mode_constraints(self) -> RuntimeScheduleConfig:
        if self.mode is ScheduleMode.DAILY:
            if not self.times:
                raise ValueError("daily schedule requires at least one time value")
            if self.weekdays:
                raise ValueError("daily schedule must not define weekdays")
        elif self.mode is ScheduleMode.WEEKLY:
            if not self.times:
                raise ValueError("weekly schedule requires at least one time value")
            if not self.weekdays:
                raise ValueError("weekly schedule requires at least one weekday")
        else:
            if self.times:
                raise ValueError("disabled schedule must not define times")
            if self.weekdays:
                raise ValueError("disabled schedule must not define weekdays")
        return self

    @property
    def is_enabled(self) -> bool:
        return self.mode is not ScheduleMode.DISABLED


def render_systemd_on_calendar(schedule: RuntimeScheduleConfig) -> tuple[str, ...]:
    if schedule.mode is ScheduleMode.DISABLED:
        return ()

    if schedule.mode is ScheduleMode.DAILY:
        return tuple(f"*-*-* {time_value}:00 {schedule.timezone}" for time_value in schedule.times)

    return tuple(
        f"{_SYSTEMD_WEEKDAY_NAMES[weekday.value]} *-*-* {time_value}:00 {schedule.timezone}"
        for weekday in schedule.weekdays
        for time_value in schedule.times
    )
