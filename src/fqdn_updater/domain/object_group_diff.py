from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from fqdn_updater.domain.keenetic import ObjectGroupState
from fqdn_updater.domain.object_group_entry import (
    ObjectGroupEntry,
    normalize_object_group_entries,
    sort_object_group_entries,
)


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


def _normalize_entries(entries: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    return tuple(
        entry.value for entry in normalize_object_group_entries(entries, field_name=field_name)
    )


class ObjectGroupDiff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    object_group_name: str
    needs_create: bool
    to_add: tuple[str, ...]
    to_remove: tuple[str, ...]
    unchanged: tuple[str, ...]
    typed_to_add: tuple[ObjectGroupEntry, ...] = ()
    typed_to_remove: tuple[ObjectGroupEntry, ...] = ()
    typed_unchanged: tuple[ObjectGroupEntry, ...] = ()
    has_changes: bool

    @model_validator(mode="before")
    @classmethod
    def _populate_compatible_entries(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        data = dict(data)
        for string_field, typed_field in (
            ("to_add", "typed_to_add"),
            ("to_remove", "typed_to_remove"),
            ("unchanged", "typed_unchanged"),
        ):
            if not data.get(typed_field) and data.get(string_field):
                data[typed_field] = normalize_object_group_entries(
                    data[string_field], field_name=string_field
                )
            if not data.get(string_field) and data.get(typed_field):
                typed_entries = normalize_object_group_entries(
                    data[typed_field], field_name=typed_field
                )
                data[typed_field] = typed_entries
                data[string_field] = tuple(entry.value for entry in typed_entries)
        return data

    @field_validator("object_group_name", mode="before")
    @classmethod
    def _validate_name(cls, value: Any) -> str:
        return _require_non_blank(str(value), "object_group_name")

    @field_validator("to_add", "to_remove", "unchanged", mode="before")
    @classmethod
    def _validate_entry_tuples(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _normalize_entries(value or (), field_name=info.field_name)

    @field_validator("typed_to_add", "typed_to_remove", "typed_unchanged", mode="before")
    @classmethod
    def _validate_typed_entry_tuples(cls, value: Any, info: Any) -> tuple[ObjectGroupEntry, ...]:
        return normalize_object_group_entries(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _validate_has_changes(self) -> ObjectGroupDiff:
        expected_has_changes = self.needs_create or bool(self.to_add) or bool(self.to_remove)
        if self.has_changes != expected_has_changes:
            raise ValueError("has_changes must match needs_create/to_add/to_remove state")
        return self


def build_object_group_diff(
    desired_entries: Iterable[str | ObjectGroupEntry],
    actual_state: ObjectGroupState,
) -> ObjectGroupDiff:
    desired_typed_entries = normalize_object_group_entries(
        desired_entries, field_name="desired_entries"
    )
    actual_typed_entries = actual_state.typed_entries if actual_state.exists else ()

    desired_set = set(desired_typed_entries)
    actual_set = set(actual_typed_entries)

    typed_to_add = sort_object_group_entries(desired_set - actual_set)
    typed_to_remove = sort_object_group_entries(actual_set - desired_set)
    typed_unchanged = sort_object_group_entries(actual_set & desired_set)
    needs_create = not actual_state.exists and bool(desired_typed_entries)

    return ObjectGroupDiff(
        object_group_name=actual_state.name,
        needs_create=needs_create,
        to_add=tuple(entry.value for entry in typed_to_add),
        to_remove=tuple(entry.value for entry in typed_to_remove),
        unchanged=tuple(entry.value for entry in typed_unchanged),
        typed_to_add=typed_to_add,
        typed_to_remove=typed_to_remove,
        typed_unchanged=typed_unchanged,
        has_changes=needs_create or bool(typed_to_add) or bool(typed_to_remove),
    )
