from __future__ import annotations

from collections.abc import Iterable
from math import ceil
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fqdn_updater.domain.keenetic_limits import (
    KEENETIC_MAX_FQDN_OBJECT_GROUP_ENTRIES,
    KEENETIC_MAX_TOTAL_FQDN_ENTRIES,
)
from fqdn_updater.domain.object_group_entry import (
    ObjectGroupEntry,
    normalize_object_group_entries,
)

MAX_MANAGED_FQDN_SHARDS = ceil(
    KEENETIC_MAX_TOTAL_FQDN_ENTRIES / KEENETIC_MAX_FQDN_OBJECT_GROUP_ENTRIES
)


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


class ObjectGroupShard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    index: int = Field(ge=0)
    entries: tuple[ObjectGroupEntry, ...] = ()

    @field_validator("name", mode="before")
    @classmethod
    def _validate_name(cls, value: Any) -> str:
        return _require_non_blank(str(value), "name")

    @field_validator("entries", mode="before")
    @classmethod
    def _validate_entries(cls, value: Any) -> tuple[ObjectGroupEntry, ...]:
        return normalize_object_group_entries(value, field_name="entries")


def build_shard_name(base_name: str, index: int) -> str:
    if index < 0:
        raise ValueError("index must be greater than or equal to zero")

    normalized_base_name = _require_non_blank(base_name, "base_name")
    if index == 0:
        return normalized_base_name
    return f"{normalized_base_name}-{index + 1}"


def managed_shard_names(base_name: str) -> tuple[str, ...]:
    return tuple(build_shard_name(base_name, index) for index in range(MAX_MANAGED_FQDN_SHARDS))


def shard_object_group_entries(
    *,
    base_name: str,
    entries: Iterable[str | ObjectGroupEntry],
) -> tuple[ObjectGroupShard, ...]:
    typed_entries = normalize_object_group_entries(entries, field_name="entries")

    shards: list[ObjectGroupShard] = []
    for index, offset in enumerate(
        range(0, len(typed_entries), KEENETIC_MAX_FQDN_OBJECT_GROUP_ENTRIES)
    ):
        shards.append(
            ObjectGroupShard(
                name=build_shard_name(base_name, index),
                index=index,
                entries=typed_entries[offset : offset + KEENETIC_MAX_FQDN_OBJECT_GROUP_ENTRIES],
            )
        )

    return tuple(shards)
