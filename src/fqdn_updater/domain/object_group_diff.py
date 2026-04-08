from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from fqdn_updater.domain.keenetic import ObjectGroupState


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


def _normalize_entries(entries: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    normalized_entries: set[str] = set()
    for entry in entries:
        normalized_entries.add(_require_non_blank(str(entry), field_name))
    return tuple(sorted(normalized_entries))


class ObjectGroupDiff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    object_group_name: str
    needs_create: bool
    to_add: tuple[str, ...]
    to_remove: tuple[str, ...]
    unchanged: tuple[str, ...]
    has_changes: bool

    @field_validator("object_group_name", mode="before")
    @classmethod
    def _validate_name(cls, value: Any) -> str:
        return _require_non_blank(str(value), "object_group_name")

    @field_validator("to_add", "to_remove", "unchanged", mode="before")
    @classmethod
    def _validate_entry_tuples(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _normalize_entries(value or (), field_name=info.field_name)

    @model_validator(mode="after")
    def _validate_has_changes(self) -> ObjectGroupDiff:
        expected_has_changes = self.needs_create or bool(self.to_add) or bool(self.to_remove)
        if self.has_changes != expected_has_changes:
            raise ValueError("has_changes must match needs_create/to_add/to_remove state")
        return self


def build_object_group_diff(
    desired_entries: Iterable[str],
    actual_state: ObjectGroupState,
) -> ObjectGroupDiff:
    normalized_desired_entries = _normalize_entries(
        desired_entries, field_name="desired_entries item"
    )
    actual_entries = actual_state.entries if actual_state.exists else ()

    desired_set = set(normalized_desired_entries)
    actual_set = set(actual_entries)

    to_add = tuple(sorted(desired_set - actual_set))
    to_remove = tuple(sorted(actual_set - desired_set))
    unchanged = tuple(sorted(actual_set & desired_set))
    needs_create = not actual_state.exists and bool(normalized_desired_entries)

    return ObjectGroupDiff(
        object_group_name=actual_state.name,
        needs_create=needs_create,
        to_add=to_add,
        to_remove=to_remove,
        unchanged=unchanged,
        has_changes=needs_create or bool(to_add) or bool(to_remove),
    )
