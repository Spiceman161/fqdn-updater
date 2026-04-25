from __future__ import annotations

import json

from fqdn_updater.application.config_management import ConfigManagementService
from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.infrastructure.config_repository import ConfigRepository

_BASE_URL = "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main"


def test_sync_builtin_services_adds_missing_builtins_and_refreshes_builtin_sources(
    tmp_path,
) -> None:
    config_path = tmp_path / "config.json"
    config = AppConfig.model_validate(
        {
            "services": [
                {
                    "key": "news",
                    "source_urls": ["https://example.com/custom-news.lst"],
                    "format": "raw_domain_list",
                    "enabled": False,
                },
                {
                    "key": "custom",
                    "source_urls": ["https://example.com/custom.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
            ],
        }
    )
    repository = ConfigRepository()
    repository.write_new(path=config_path, config=config)

    result = ConfigManagementService(repository=repository).sync_builtin_services(path=config_path)

    assert result.added_service_keys[:5] == (
        "anime",
        "block",
        "block_p2p_streaming",
        "block_vpn_proxy_privacy",
        "block_dev_hosting_security",
    )
    assert "geoblock_ai" in result.added_service_keys
    news = next(service for service in result.config.services if service.key == "news")
    assert news.enabled is False
    assert str(news.resolved_sources[0].url) == f"{_BASE_URL}/Categories/news.lst"
    assert result.config.services[-1].key == "custom"

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    service_keys = [service["key"] for service in payload["services"]]
    assert service_keys[:5] == [
        "anime",
        "block",
        "block_p2p_streaming",
        "block_vpn_proxy_privacy",
        "block_dev_hosting_security",
    ]
    assert "geoblock_ai" in service_keys
    assert service_keys.index("news") < service_keys.index("cloudflare")
    assert service_keys[-1] == "custom"


def test_sync_builtin_services_removes_stale_roblox_ipv6_source(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = AppConfig.model_validate(
        {
            "services": [
                {
                    "key": "roblox",
                    "sources": [
                        {
                            "url": f"{_BASE_URL}/Services/roblox.lst",
                            "format": "raw_domain_list",
                        },
                        {
                            "url": f"{_BASE_URL}/Subnets/IPv4/roblox.lst",
                            "format": "raw_cidr_list",
                        },
                        {
                            "url": f"{_BASE_URL}/Subnets/IPv6/roblox.lst",
                            "format": "raw_cidr_list",
                        },
                    ],
                    "enabled": False,
                },
            ],
        }
    )
    repository = ConfigRepository()
    repository.write_new(path=config_path, config=config)

    result = ConfigManagementService(repository=repository).sync_builtin_services(path=config_path)

    roblox = next(service for service in result.config.services if service.key == "roblox")
    assert roblox.enabled is False
    assert [str(source.url) for source in roblox.resolved_sources] == [
        f"{_BASE_URL}/Services/roblox.lst",
        f"{_BASE_URL}/Subnets/IPv4/roblox.lst",
    ]
