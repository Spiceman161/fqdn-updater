from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_OBJECT_GROUP_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


def _require_object_group_name(value: str, field_name: str) -> str:
    normalized_value = _require_non_blank(value, field_name)
    if not _OBJECT_GROUP_PATTERN.fullmatch(normalized_value):
        raise ValueError(
            f"{field_name} must use lowercase letters, digits, underscores, or hyphens"
        )
    return normalized_value


class ObjectGroupState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    entries: tuple[str, ...] = Field(default_factory=tuple)
    exists: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: Any) -> str:
        return _require_object_group_name(str(value), "name")

    @field_validator("entries", mode="before")
    @classmethod
    def _validate_entries(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()

        normalized_entries: list[str] = []
        seen: set[str] = set()
        for item in value:
            normalized_item = _require_non_blank(str(item), "entries item")
            if normalized_item in seen:
                continue
            seen.add(normalized_item)
            normalized_entries.append(normalized_item)
        return tuple(sorted(normalized_entries))

    @model_validator(mode="after")
    def _validate_absent_group_state(self) -> ObjectGroupState:
        if not self.exists and self.entries:
            raise ValueError("entries must be empty when exists is false")
        return self


class RouteBindingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    object_group_name: str
    route_target_type: Literal["interface", "gateway"]
    route_target_value: str
    route_interface: str | None = None
    auto: bool = True
    exclusive: bool = False

    @field_validator("object_group_name", mode="before")
    @classmethod
    def _validate_object_group_name(cls, value: Any) -> str:
        return _require_object_group_name(str(value), "object_group_name")

    @field_validator("route_target_value", "route_interface", mode="before")
    @classmethod
    def _validate_text_fields(cls, value: Any, info: Any) -> str | None:
        if value is None:
            return None
        return _require_non_blank(str(value), info.field_name)

    @model_validator(mode="after")
    def _validate_route_target_shape(self) -> RouteBindingSpec:
        if self.route_target_type == "interface" and self.route_interface is not None:
            raise ValueError("route_interface must be unset when route_target_type is interface")
        return self


class DnsProxyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
