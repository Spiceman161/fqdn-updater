from __future__ import annotations

from typing import Literal

from fqdn_updater.domain.object_group_entry import (
    ObjectGroupEntry,
    canonicalize_domain,
    canonicalize_network,
    sort_object_group_entries,
)

SourceFormat = Literal["raw_domain_list", "raw_cidr_list", "mixed"]


def normalize_entries(raw_text: str, source_format: SourceFormat) -> tuple[str, ...]:
    return tuple(
        sorted(
            entry.value
            for entry in normalize_typed_entries(raw_text=raw_text, source_format=source_format)
        )
    )


def normalize_typed_entries(
    raw_text: str, source_format: SourceFormat
) -> tuple[ObjectGroupEntry, ...]:
    normalized_entries: set[ObjectGroupEntry] = set()

    for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
        stripped_line = raw_line.strip()
        if not stripped_line or stripped_line.startswith("#"):
            continue

        normalized_entries.add(
            _normalize_entry(
                entry=stripped_line,
                source_format=source_format,
                line_number=line_number,
            )
        )

    return sort_object_group_entries(normalized_entries)


def _normalize_entry(entry: str, source_format: SourceFormat, line_number: int) -> ObjectGroupEntry:
    if source_format == "raw_domain_list":
        return _normalize_domain(entry=entry, line_number=line_number)
    if source_format == "raw_cidr_list":
        return _normalize_network(entry=entry, line_number=line_number)
    return _normalize_mixed_entry(entry=entry, line_number=line_number)


def _normalize_mixed_entry(entry: str, line_number: int) -> ObjectGroupEntry:
    try:
        return _normalize_network(entry=entry, line_number=line_number)
    except ValueError:
        return _normalize_domain(entry=entry, line_number=line_number)


def _normalize_network(entry: str, line_number: int) -> ObjectGroupEntry:
    try:
        network = canonicalize_network(entry)
    except ValueError as exc:
        raise ValueError(f"line {line_number}: invalid CIDR entry '{entry}'") from exc
    return ObjectGroupEntry.from_network(str(network))


def _normalize_domain(entry: str, line_number: int) -> ObjectGroupEntry:
    try:
        return ObjectGroupEntry.from_domain(canonicalize_domain(entry))
    except ValueError as exc:
        raise ValueError(f"line {line_number}: {exc}") from exc
