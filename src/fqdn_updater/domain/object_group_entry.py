from __future__ import annotations

import ipaddress
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

ObjectGroupEntryKind = Literal["domain", "ipv4_network", "ipv6_network"]

_DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_KIND_ORDER: dict[ObjectGroupEntryKind, int] = {
    "domain": 0,
    "ipv4_network": 1,
    "ipv6_network": 2,
}


class ObjectGroupEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str
    kind: ObjectGroupEntryKind

    @field_validator("value", mode="before")
    @classmethod
    def _validate_value(cls, value: Any) -> str:
        normalized_value = str(value).strip()
        if not normalized_value:
            raise ValueError("value must not be empty")
        return normalized_value

    @model_validator(mode="after")
    def _canonicalize_value(self) -> ObjectGroupEntry:
        if self.kind == "domain":
            canonical_value = canonicalize_domain(self.value)
        else:
            expected_version = 4 if self.kind == "ipv4_network" else 6
            network = canonicalize_network(self.value)
            if network.version != expected_version:
                raise ValueError(f"value must be an IPv{expected_version} network")
            canonical_value = str(network)

        object.__setattr__(self, "value", canonical_value)
        return self

    @classmethod
    def from_domain(cls, value: str) -> ObjectGroupEntry:
        return cls(value=value, kind="domain")

    @classmethod
    def from_network(cls, value: str) -> ObjectGroupEntry:
        network = canonicalize_network(value)
        kind: ObjectGroupEntryKind = "ipv4_network" if network.version == 4 else "ipv6_network"
        return cls(value=str(network), kind=kind)

    @classmethod
    def classify(cls, value: str) -> ObjectGroupEntry:
        try:
            return cls.from_network(value)
        except ValueError:
            return cls.from_domain(value)

    @property
    def sort_key(self) -> tuple[int, str | int, int]:
        if self.kind == "domain":
            return (_KIND_ORDER[self.kind], self.value, 0)

        network = ipaddress.ip_network(self.value, strict=False)
        return (_KIND_ORDER[self.kind], int(network.network_address), network.prefixlen)


def canonicalize_domain(value: str) -> str:
    normalized_value = value.strip().lower().rstrip(".")
    if not normalized_value:
        raise ValueError("domain entry must not be empty")
    if "/" in normalized_value:
        raise ValueError(f"invalid domain entry '{value}'")
    if any(character.isspace() for character in normalized_value):
        raise ValueError(f"invalid domain entry '{value}'")

    labels = normalized_value.split(".")
    if len(labels) < 2:
        raise ValueError(f"invalid domain entry '{value}'")

    for label in labels:
        if not _DOMAIN_LABEL_PATTERN.fullmatch(label):
            raise ValueError(f"invalid domain entry '{value}'")

    return normalized_value


def canonicalize_network(value: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    return ipaddress.ip_network(value, strict=False)


def sort_object_group_entries(
    entries: tuple[ObjectGroupEntry, ...] | list[ObjectGroupEntry] | set[ObjectGroupEntry],
) -> tuple[ObjectGroupEntry, ...]:
    return tuple(sorted(entries, key=lambda entry: entry.sort_key))


def normalize_object_group_entries(
    entries: Any,
    *,
    field_name: str,
) -> tuple[ObjectGroupEntry, ...]:
    if entries is None:
        return ()

    normalized_entries: set[ObjectGroupEntry] = set()
    for item in entries:
        if isinstance(item, ObjectGroupEntry):
            normalized_entry = item
        elif isinstance(item, dict):
            normalized_entry = ObjectGroupEntry.model_validate(item)
        else:
            try:
                normalized_entry = ObjectGroupEntry.classify(str(item))
            except ValueError as exc:
                raise ValueError(f"{field_name} item is invalid: {exc}") from exc
        normalized_entries.add(normalized_entry)
    return sort_object_group_entries(normalized_entries)
