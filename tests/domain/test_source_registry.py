from __future__ import annotations

from fqdn_updater.domain.source_registry import builtin_service_definitions


def test_builtin_service_registry_contains_expected_v1_services() -> None:
    services = builtin_service_definitions()
    services_by_key = {service.key: service for service in services}

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
    assert [str(source.url) for source in services_by_key["meta"].resolved_sources] == [
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Services/meta.lst",
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Subnets/IPv4/meta.lst",
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Subnets/IPv6/meta.lst",
    ]
    assert [source.format for source in services_by_key["meta"].resolved_sources] == [
        "raw_domain_list",
        "raw_cidr_list",
        "raw_cidr_list",
    ]
    assert [str(source.url) for source in services_by_key["telegram"].resolved_sources] == [
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Services/telegram.lst",
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Subnets/IPv4/telegram.lst",
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Subnets/IPv6/telegram.lst",
    ]
    assert [source.format for source in services_by_key["telegram"].resolved_sources] == [
        "raw_domain_list",
        "raw_cidr_list",
        "raw_cidr_list",
    ]


def test_builtin_service_registry_returns_detached_copies() -> None:
    first = builtin_service_definitions()
    second = builtin_service_definitions()

    first[0].description = "mutated"

    assert second[0].description != "mutated"
