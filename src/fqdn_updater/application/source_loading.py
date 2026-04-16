from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from fqdn_updater.domain.config_schema import ServiceDefinitionConfig
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry, sort_object_group_entries
from fqdn_updater.domain.source_loading import (
    NormalizedServiceSource,
    ServiceSourceFailure,
    SourceLoadReport,
)
from fqdn_updater.domain.source_normalizer import normalize_typed_entries


class RawSourceFetcher(Protocol):
    def fetch_text(self, url: str) -> str:
        """Fetch raw source text for a single URL."""


class SourceLoadingService:
    def __init__(self, fetcher: RawSourceFetcher) -> None:
        self._fetcher = fetcher

    def load_enabled_services(
        self, services: Sequence[ServiceDefinitionConfig]
    ) -> SourceLoadReport:
        loaded: list[NormalizedServiceSource] = []
        failed: list[ServiceSourceFailure] = []

        for service in services:
            if not service.enabled:
                continue

            try:
                loaded.append(self._load_service(service))
            except _ServiceLoadFailure as exc:
                failed.append(
                    ServiceSourceFailure(
                        service_key=service.key,
                        source_url=exc.source_url,
                        message=exc.message,
                    )
                )

        return SourceLoadReport(loaded=tuple(loaded), failed=tuple(failed))

    def _load_service(self, service: ServiceDefinitionConfig) -> NormalizedServiceSource:
        merged_entries: set[ObjectGroupEntry] = set()

        for source in service.resolved_sources:
            source_url_text = str(source.url)
            try:
                raw_text = self._fetcher.fetch_text(source_url_text)
                normalized_entries = normalize_typed_entries(
                    raw_text=raw_text,
                    source_format=source.format,
                )
            except Exception as exc:
                raise _ServiceLoadFailure(
                    source_url=source_url_text,
                    message=str(exc),
                ) from exc

            merged_entries.update(normalized_entries)

        typed_entries = sort_object_group_entries(merged_entries)
        return NormalizedServiceSource(
            service_key=service.key,
            typed_entries=typed_entries,
            entries=tuple(entry.value for entry in typed_entries),
        )


class _ServiceLoadFailure(Exception):
    def __init__(self, source_url: str, message: str) -> None:
        super().__init__(message)
        self.source_url = source_url
        self.message = message
