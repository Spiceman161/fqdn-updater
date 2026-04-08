from __future__ import annotations

from fqdn_updater.domain.source_registry import builtin_service_definitions


def test_builtin_service_registry_contains_expected_v1_services() -> None:
    services = builtin_service_definitions()

    assert [service.key for service in services] == [
        "news",
        "hdrezka",
        "meta",
        "tiktok",
        "twitter",
        "youtube",
        "discord",
        "cloudflare",
        "telegram",
        "google_meet",
        "google_ai",
    ]
    assert [str(url) for url in services[-1].source_urls] == [
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Services/google_ai.lst"
    ]
    assert services[-1].format == "raw_domain_list"


def test_builtin_service_registry_returns_detached_copies() -> None:
    first = builtin_service_definitions()
    second = builtin_service_definitions()

    first[0].description = "mutated"

    assert second[0].description != "mutated"
