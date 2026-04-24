from __future__ import annotations

from fqdn_updater.domain.config_schema import ServiceDefinitionConfig
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.source_loading import (
    NormalizedServiceSource,
    ServiceSourceFailure,
    SourceLoadReport,
)
from fqdn_updater.infrastructure.service_count_cache import (
    CachingSourceLoadingService,
    ServiceCountCacheRepository,
    ServiceEntryCountSnapshot,
)


class _StubSourceLoader:
    def __init__(self, report: SourceLoadReport) -> None:
        self.report = report
        self.calls: list[list[str]] = []

    def load_enabled_services(self, services: list[ServiceDefinitionConfig]) -> SourceLoadReport:
        self.calls.append([service.key for service in services])
        return self.report


def test_service_count_cache_repository_reads_missing_file_as_empty(tmp_path) -> None:
    repository = ServiceCountCacheRepository()

    assert repository.read(path=tmp_path / "service-count-cache.json") == {}


def test_service_count_cache_repository_updates_loaded_services_and_preserves_existing_entries(
    tmp_path,
) -> None:
    repository = ServiceCountCacheRepository()
    cache_path = tmp_path / "service-count-cache.json"
    repository.write(
        path=cache_path,
        counts={
            "telegram": ServiceEntryCountSnapshot(domains=1, ipv4=1, ipv6=0),
            "youtube": ServiceEntryCountSnapshot(domains=2, ipv4=0, ipv6=1),
        },
    )

    repository.update_from_report(
        path=cache_path,
        report=SourceLoadReport(
            loaded=(
                NormalizedServiceSource(
                    service_key="google_ai",
                    typed_entries=(
                        ObjectGroupEntry.from_domain("one.example"),
                        ObjectGroupEntry.from_network("192.0.2.0/24"),
                    ),
                ),
            ),
            failed=(
                ServiceSourceFailure(
                    service_key="youtube",
                    source_url="https://example.com/youtube.lst",
                    message="upstream timeout",
                ),
            ),
        ),
    )

    assert repository.read(path=cache_path) == {
        "telegram": ServiceEntryCountSnapshot(domains=1, ipv4=1, ipv6=0),
        "youtube": ServiceEntryCountSnapshot(domains=2, ipv4=0, ipv6=1),
        "google_ai": ServiceEntryCountSnapshot(domains=1, ipv4=1, ipv6=0),
    }


def test_caching_source_loading_service_writes_loaded_counts_to_cache(tmp_path) -> None:
    repository = ServiceCountCacheRepository()
    cache_path = tmp_path / "service-count-cache.json"
    source_loader = _StubSourceLoader(
        report=SourceLoadReport(
            loaded=(
                NormalizedServiceSource(
                    service_key="telegram",
                    typed_entries=(
                        ObjectGroupEntry.from_domain("one.example"),
                        ObjectGroupEntry.from_network("192.0.2.0/24"),
                        ObjectGroupEntry.from_network("2001:db8::/32"),
                    ),
                ),
            )
        )
    )
    caching_loader = CachingSourceLoadingService(
        source_loader=source_loader,
        cache_repository=repository,
        cache_path=cache_path,
    )

    report = caching_loader.load_enabled_services(
        [
            ServiceDefinitionConfig(
                key="telegram",
                source_urls=["https://example.com/telegram.lst"],
                format="raw_domain_list",
                enabled=True,
            )
        ]
    )

    assert source_loader.calls == [["telegram"]]
    assert report.loaded[0].service_key == "telegram"
    assert repository.read(path=cache_path) == {
        "telegram": ServiceEntryCountSnapshot(domains=1, ipv4=1, ipv6=1),
    }
