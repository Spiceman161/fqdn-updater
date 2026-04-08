from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


class NormalizedServiceSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_key: str
    entries: tuple[str, ...] = Field(default_factory=tuple)

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
