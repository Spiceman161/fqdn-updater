from __future__ import annotations

from fqdn_updater.application.source_loading import SourceLoadingService
from fqdn_updater.domain.config_schema import ServiceDefinitionConfig


class FakeFetcher:
    def __init__(self, payloads: dict[str, str], failing_urls: set[str] | None = None) -> None:
        self._payloads = payloads
        self._failing_urls = failing_urls or set()
        self.calls: list[str] = []

    def fetch_text(self, url: str) -> str:
        self.calls.append(url)
        if url in self._failing_urls:
            raise RuntimeError(f"boom for {url}")
        return self._payloads[url]


def test_load_enabled_services_merges_multiple_urls_deterministically() -> None:
    service = ServiceDefinitionConfig.model_validate(
        {
            "key": "telegram",
            "source_urls": [
                "https://example.com/one.lst",
                "https://example.com/two.lst",
            ],
            "format": "raw_domain_list",
            "enabled": True,
        }
    )
    fetcher = FakeFetcher(
        payloads={
            "https://example.com/one.lst": "B.example.com\n",
            "https://example.com/two.lst": "a.example.com\nb.example.com\n",
        }
    )

    report = SourceLoadingService(fetcher=fetcher).load_enabled_services([service])

    assert report.failed == ()
    assert report.loaded[0].service_key == "telegram"
    assert report.loaded[0].entries == ("a.example.com", "b.example.com")
    assert [(entry.kind, entry.value) for entry in report.loaded[0].typed_entries] == [
        ("domain", "a.example.com"),
        ("domain", "b.example.com"),
    ]


def test_load_enabled_services_supports_per_source_mixed_formats() -> None:
    service = ServiceDefinitionConfig.model_validate(
        {
            "key": "telegram",
            "sources": [
                {
                    "url": "https://example.com/telegram-domains.lst",
                    "format": "raw_domain_list",
                },
                {
                    "url": "https://example.com/telegram-v4.lst",
                    "format": "raw_cidr_list",
                },
                {
                    "url": "https://example.com/telegram-mixed.lst",
                    "format": "mixed",
                },
            ],
            "enabled": True,
        }
    )
    fetcher = FakeFetcher(
        payloads={
            "https://example.com/telegram-domains.lst": "B.example.com\n",
            "https://example.com/telegram-v4.lst": "10.0.0.1/24\n",
            "https://example.com/telegram-mixed.lst": "2001:DB8::1/64\na.example.com\n",
        }
    )

    report = SourceLoadingService(fetcher=fetcher).load_enabled_services([service])

    assert report.failed == ()
    assert report.loaded[0].service_key == "telegram"
    assert report.loaded[0].entries == (
        "10.0.0.0/24",
        "2001:db8::/64",
        "a.example.com",
        "b.example.com",
    )
    assert [(entry.kind, entry.value) for entry in report.loaded[0].typed_entries] == [
        ("domain", "a.example.com"),
        ("domain", "b.example.com"),
        ("ipv4_network", "10.0.0.0/24"),
        ("ipv6_network", "2001:db8::/64"),
    ]
    assert fetcher.calls == [
        "https://example.com/telegram-domains.lst",
        "https://example.com/telegram-v4.lst",
        "https://example.com/telegram-mixed.lst",
    ]


def test_load_enabled_services_fails_service_atomically_when_any_url_fails() -> None:
    service = ServiceDefinitionConfig.model_validate(
        {
            "key": "telegram",
            "source_urls": [
                "https://example.com/one.lst",
                "https://example.com/two.lst",
            ],
            "format": "raw_domain_list",
            "enabled": True,
        }
    )
    fetcher = FakeFetcher(
        payloads={
            "https://example.com/one.lst": "a.example.com\n",
            "https://example.com/two.lst": "b.example.com\n",
        },
        failing_urls={"https://example.com/two.lst"},
    )

    report = SourceLoadingService(fetcher=fetcher).load_enabled_services([service])

    assert report.loaded == ()
    assert len(report.failed) == 1
    assert report.failed[0].service_key == "telegram"
    assert report.failed[0].source_url == "https://example.com/two.lst"
    assert report.failed[0].message == "boom for https://example.com/two.lst"


def test_load_enabled_services_reports_failing_source_url_for_per_source_config() -> None:
    service = ServiceDefinitionConfig.model_validate(
        {
            "key": "telegram",
            "sources": [
                {
                    "url": "https://example.com/telegram-domains.lst",
                    "format": "raw_domain_list",
                },
                {
                    "url": "https://example.com/telegram-v4.lst",
                    "format": "raw_cidr_list",
                },
            ],
            "enabled": True,
        }
    )
    fetcher = FakeFetcher(
        payloads={
            "https://example.com/telegram-domains.lst": "a.example.com\n",
            "https://example.com/telegram-v4.lst": "10.0.0.1/24\n",
        },
        failing_urls={"https://example.com/telegram-v4.lst"},
    )

    report = SourceLoadingService(fetcher=fetcher).load_enabled_services([service])

    assert report.loaded == ()
    assert len(report.failed) == 1
    assert report.failed[0].service_key == "telegram"
    assert report.failed[0].source_url == "https://example.com/telegram-v4.lst"
    assert report.failed[0].message == "boom for https://example.com/telegram-v4.lst"


def test_load_enabled_services_skips_disabled_services() -> None:
    service = ServiceDefinitionConfig.model_validate(
        {
            "key": "telegram",
            "source_urls": ["https://example.com/one.lst"],
            "format": "raw_domain_list",
            "enabled": False,
        }
    )
    fetcher = FakeFetcher(payloads={"https://example.com/one.lst": "a.example.com\n"})

    report = SourceLoadingService(fetcher=fetcher).load_enabled_services([service])

    assert report.loaded == ()
    assert report.failed == ()
    assert fetcher.calls == []


def test_load_enabled_services_reports_normalization_failures() -> None:
    service = ServiceDefinitionConfig.model_validate(
        {
            "key": "telegram",
            "source_urls": ["https://example.com/one.lst"],
            "format": "raw_domain_list",
            "enabled": True,
        }
    )
    fetcher = FakeFetcher(payloads={"https://example.com/one.lst": "bad domain\n"})

    report = SourceLoadingService(fetcher=fetcher).load_enabled_services([service])

    assert report.loaded == ()
    assert report.failed[0].service_key == "telegram"
    assert report.failed[0].source_url == "https://example.com/one.lst"
    assert "invalid domain entry" in report.failed[0].message
