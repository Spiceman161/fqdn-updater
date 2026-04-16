from __future__ import annotations

from fqdn_updater.domain.source_registry import builtin_service_definitions


def test_builtin_service_registry_contains_expected_v1_services() -> None:
    services = builtin_service_definitions()
    services_by_key = {service.key: service for service in services}

    assert [service.key for service in services] == [
        "news",
        "cloudflare",
        "cloudfront",
        "digitalocean",
        "discord",
        "google_ai",
        "google_meet",
        "google_play",
        "hdrezka",
        "hetzner",
        "meta",
        "ovh",
        "roblox",
        "telegram",
        "tiktok",
        "twitter",
        "youtube",
    ]
    assert [str(source.url) for source in services_by_key["cloudflare"].resolved_sources] == [
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Services/cloudflare.lst",
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Subnets/IPv4/cloudflare.lst",
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Subnets/IPv6/cloudflare.lst",
    ]
    assert [source.format for source in services_by_key["cloudflare"].resolved_sources] == [
        "raw_domain_list",
        "raw_cidr_list",
        "raw_cidr_list",
    ]
    assert [str(source.url) for source in services_by_key["google_ai"].resolved_sources] == [
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Services/google_ai.lst"
    ]
    assert [source.format for source in services_by_key["google_ai"].resolved_sources] == [
        "raw_domain_list"
    ]
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
    assert [str(source.url) for source in services_by_key["ovh"].resolved_sources] == [
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Services/ovh.lst",
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Subnets/IPv4/ovh.lst",
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Subnets/IPv6/ovh.lst",
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
