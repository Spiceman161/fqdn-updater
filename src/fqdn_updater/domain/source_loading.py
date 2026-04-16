from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fqdn_updater.domain.object_group_entry import (
    ObjectGroupEntry,
    normalize_object_group_entries,
)


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


class NormalizedServiceSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_key: str
    entries: tuple[str, ...] = Field(default_factory=tuple)
    typed_entries: tuple[ObjectGroupEntry, ...] = Field(default_factory=tuple)

    @model_validator(mode="before")
    @classmethod
    def _populate_compatible_entries(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        if not data.get("typed_entries") and data.get("entries"):
            data = dict(data)
            data["typed_entries"] = normalize_object_group_entries(
                data["entries"], field_name="entries"
            )
        if not data.get("entries") and data.get("typed_entries"):
            data = dict(data)
            typed_entries = normalize_object_group_entries(
                data["typed_entries"], field_name="typed_entries"
            )
            data["typed_entries"] = typed_entries
            data["entries"] = tuple(entry.value for entry in typed_entries)
        return data

    @field_validator("service_key", mode="before")
    @classmethod
    def _validate_service_key(cls, value: Any) -> str:
        return _require_non_blank(str(value), "service_key")

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

    @field_validator("typed_entries", mode="before")
    @classmethod
    def _validate_typed_entries(cls, value: Any) -> tuple[ObjectGroupEntry, ...]:
        return normalize_object_group_entries(value, field_name="typed_entries")


class ServiceSourceFailure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_key: str
    source_url: str
    message: str

    @field_validator("service_key", "source_url", "message", mode="before")
    @classmethod
    def _validate_text_fields(cls, value: Any, info: Any) -> str:
        return _require_non_blank(str(value), info.field_name)


class SourceLoadReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    loaded: tuple[NormalizedServiceSource, ...] = Field(default_factory=tuple)
    failed: tuple[ServiceSourceFailure, ...] = Field(default_factory=tuple)
