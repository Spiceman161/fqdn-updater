from __future__ import annotations

import ipaddress
import re
from typing import Literal

SourceFormat = Literal["raw_domain_list", "raw_cidr_list", "mixed"]

_DOMAIN_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_entries(raw_text: str, source_format: SourceFormat) -> tuple[str, ...]:
    normalized_entries: set[str] = set()

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

    return tuple(sorted(normalized_entries))


def _normalize_entry(entry: str, source_format: SourceFormat, line_number: int) -> str:
    if source_format == "raw_domain_list":
        return _normalize_domain(entry=entry, line_number=line_number)
    if source_format == "raw_cidr_list":
        return _normalize_network(entry=entry, line_number=line_number)
    return _normalize_mixed_entry(entry=entry, line_number=line_number)


def _normalize_mixed_entry(entry: str, line_number: int) -> str:
    try:
        return _normalize_network(entry=entry, line_number=line_number)
    except ValueError:
        return _normalize_domain(entry=entry, line_number=line_number)


def _normalize_network(entry: str, line_number: int) -> str:
    try:
        return str(ipaddress.ip_network(entry, strict=False))
    except ValueError as exc:
        raise ValueError(f"line {line_number}: invalid CIDR entry '{entry}'") from exc


def _normalize_domain(entry: str, line_number: int) -> str:
    normalized_entry = entry.strip().lower().rstrip(".")
    if not normalized_entry:
        raise ValueError(f"line {line_number}: domain entry must not be empty")
    if "/" in normalized_entry:
        raise ValueError(f"line {line_number}: invalid domain entry '{entry}'")
    if any(character.isspace() for character in normalized_entry):
        raise ValueError(f"line {line_number}: invalid domain entry '{entry}'")

    labels = normalized_entry.split(".")
    if len(labels) < 2:
        raise ValueError(f"line {line_number}: invalid domain entry '{entry}'")

    for label in labels:
        if not _DOMAIN_LABEL_PATTERN.fullmatch(label):
            raise ValueError(f"line {line_number}: invalid domain entry '{entry}'")

    return normalized_entry
