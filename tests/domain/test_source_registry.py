from __future__ import annotations

from fqdn_updater.domain.source_registry import builtin_service_definitions


def test_builtin_service_registry_contains_expected_v1_services() -> None:
    services = builtin_service_definitions()
    services_by_key = {service.key: service for service in services}

    assert [service.key for service in services] == [
        "anime",
        "block",
        "block_p2p_streaming",
        "block_vpn_proxy_privacy",
        "block_dev_hosting_security",
        "block_finance_shopping",
        "block_social_creators",
        "block_news_politics",
        "block_other",
        "geoblock",
        "geoblock_ai",
        "geoblock_dev_cloud_saas",
        "geoblock_media_games",
        "geoblock_shopping_travel",
        "geoblock_enterprise_hardware",
        "geoblock_security_networking",
        "geoblock_finance_payments",
        "geoblock_health_reference",
        "geoblock_other",
        "hodca",
        "news",
        "porn",
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
    for category_key in ("anime", "block", "geoblock", "hodca", "news", "porn"):
        assert [str(source.url) for source in services_by_key[category_key].resolved_sources] == [
            f"https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/"
            f"Categories/{category_key}.lst"
        ]
        assert [source.format for source in services_by_key[category_key].resolved_sources] == [
            "raw_domain_list"
        ]
    block_p2p_source = services_by_key["block_p2p_streaming"].resolved_sources[0]
    assert str(block_p2p_source.url) == (
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/"
        "Categories/block.lst"
    )
    assert block_p2p_source.include_domain_suffixes[:3] == [
        "1337x.to",
        "anidub.com",
        "annas-archive.org",
    ]
    assert block_p2p_source.exclude_domain_suffixes == []
    block_other_source = services_by_key["block_other"].resolved_sources[0]
    assert str(block_other_source.url) == (
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/"
        "Categories/block.lst"
    )
    assert "1337x.to" in block_other_source.exclude_domain_suffixes
    assert block_other_source.include_domain_suffixes == []
    geoblock_ai_source = services_by_key["geoblock_ai"].resolved_sources[0]
    assert str(geoblock_ai_source.url) == (
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/"
        "Categories/geoblock.lst"
    )
    assert "openai.com" in geoblock_ai_source.include_domain_suffixes
    geoblock_other_source = services_by_key["geoblock_other"].resolved_sources[0]
    assert str(geoblock_other_source.url) == (
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/"
        "Categories/geoblock.lst"
    )
    assert "openai.com" in geoblock_other_source.exclude_domain_suffixes
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
    assert [str(source.url) for source in services_by_key["roblox"].resolved_sources] == [
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Services/roblox.lst",
        "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Subnets/IPv4/roblox.lst",
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
