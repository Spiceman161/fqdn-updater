from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from fqdn_updater.domain.config_schema import ServiceDefinitionConfig
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.source_loading import SourceLoadReport

SERVICE_COUNT_CACHE_FILE_NAME = "service-count-cache.json"


@dataclass(frozen=True)
class ServiceEntryCountSnapshot:
    domains: int
    ipv4: int
    ipv6: int


class SourceLoader(Protocol):
    def load_enabled_services(
        self, services: Sequence[ServiceDefinitionConfig]
    ) -> SourceLoadReport: ...


class ServiceCountCacheRepository:
    def read(self, *, path: Path) -> dict[str, ServiceEntryCountSnapshot]:
        if not path.exists():
            return {}

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        if not isinstance(payload, dict):
            return {}
        raw_services = payload.get("services", payload)
        if not isinstance(raw_services, dict):
            return {}

        counts: dict[str, ServiceEntryCountSnapshot] = {}
        for service_key, raw_counts in raw_services.items():
            if not isinstance(service_key, str):
                continue
            snapshot = _parse_service_entry_count_snapshot(raw_counts)
            if snapshot is None:
                continue
            counts[service_key] = snapshot
        return counts

    def write(
        self,
        *,
        path: Path,
        counts: Mapping[str, ServiceEntryCountSnapshot],
    ) -> None:
        payload = {
            "services": {
                service_key: {
                    "domains": snapshot.domains,
                    "ipv4": snapshot.ipv4,
                    "ipv6": snapshot.ipv6,
                }
                for service_key, snapshot in sorted(counts.items())
            }
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def update_from_report(
        self,
        *,
        path: Path,
        report: SourceLoadReport,
    ) -> dict[str, ServiceEntryCountSnapshot]:
        counts = self.read(path=path)
        counts.update(service_entry_counts_from_report(report=report))
        self.write(path=path, counts=counts)
        return counts


class CachingSourceLoadingService:
    def __init__(
        self,
        *,
        source_loader: SourceLoader,
        cache_repository: ServiceCountCacheRepository,
        cache_path: Path,
    ) -> None:
        self._source_loader = source_loader
        self._cache_repository = cache_repository
        self._cache_path = cache_path

    def load_enabled_services(
        self, services: Sequence[ServiceDefinitionConfig]
    ) -> SourceLoadReport:
        report = self._source_loader.load_enabled_services(services)
        self._cache_repository.update_from_report(path=self._cache_path, report=report)
        return report


def resolve_service_count_cache_path(*, config_path: Path, artifacts_dir: str) -> Path:
    resolved_artifacts_dir = _resolve_config_relative_path(
        config_path=config_path,
        configured_path=artifacts_dir,
    )
    return resolved_artifacts_dir.parent / SERVICE_COUNT_CACHE_FILE_NAME


def service_entry_counts_from_report(
    *,
    report: SourceLoadReport,
) -> dict[str, ServiceEntryCountSnapshot]:
    return {
        source.service_key: count_service_entries(source.typed_entries) for source in report.loaded
    }


def count_service_entries(entries: tuple[ObjectGroupEntry, ...]) -> ServiceEntryCountSnapshot:
    return ServiceEntryCountSnapshot(
        domains=sum(1 for entry in entries if entry.kind == "domain"),
        ipv4=sum(1 for entry in entries if entry.kind == "ipv4_network"),
        ipv6=sum(1 for entry in entries if entry.kind == "ipv6_network"),
    )


def _parse_service_entry_count_snapshot(value: Any) -> ServiceEntryCountSnapshot | None:
    if not isinstance(value, dict):
        return None

    domains = _parse_non_negative_int(value.get("domains"))
    ipv4 = _parse_non_negative_int(value.get("ipv4"))
    ipv6 = _parse_non_negative_int(value.get("ipv6"))
    if domains is None or ipv4 is None or ipv6 is None:
        return None
    return ServiceEntryCountSnapshot(domains=domains, ipv4=ipv4, ipv6=ipv6)


def _parse_non_negative_int(value: Any) -> int | None:
    if not isinstance(value, int) or value < 0:
        return None
    return value


def _resolve_config_relative_path(*, config_path: Path, configured_path: str) -> Path:
    path = Path(configured_path)
    if path.is_absolute():
        return path
    return config_path.parent / path
