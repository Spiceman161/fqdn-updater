from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fqdn_updater.application.config_management import ConfigManagementService
from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.run_artifact import (
    RouterResultStatus,
    RouterRunResult,
    RunArtifact,
    RunMode,
    RunStatus,
    RunTrigger,
    ServiceResultStatus,
    ServiceRunResult,
)
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
        "direct_ru_outside",
        "direct_custom",
        "anime",
        "block",
        "block_p2p_streaming",
    )
    assert "geoblock_ai" in result.added_service_keys
    news = next(service for service in result.config.services if service.key == "news")
    assert news.enabled is False
    assert str(news.resolved_sources[0].url) == f"{_BASE_URL}/Categories/news.lst"
    assert result.config.services[-1].key == "custom"

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    service_keys = [service["key"] for service in payload["services"]]
    assert service_keys[:5] == [
        "direct_ru_outside",
        "direct_custom",
        "anime",
        "block",
        "block_p2p_streaming",
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


def test_prune_synced_disabled_mappings_removes_successful_cleanup_entries(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = AppConfig.model_validate(
        {
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_PASSWORD",
                    "enabled": True,
                }
            ],
            "services": [
                {
                    "key": "telegram",
                    "source_urls": ["https://example.com/telegram.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
                {
                    "key": "youtube",
                    "source_urls": ["https://example.com/youtube.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
                {
                    "key": "discord",
                    "source_urls": ["https://example.com/discord.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
            ],
            "mappings": [
                {
                    "router_id": "router-1",
                    "service_key": "telegram",
                    "object_group_name": "fqdn-telegram",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                    "enabled": True,
                },
                {
                    "router_id": "router-1",
                    "service_key": "youtube",
                    "object_group_name": "fqdn-youtube",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                    "enabled": False,
                },
                {
                    "router_id": "router-1",
                    "service_key": "discord",
                    "object_group_name": "fqdn-discord",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                    "enabled": False,
                },
            ],
        }
    )
    repository = ConfigRepository()
    repository.write_new(path=config_path, config=config)

    result = ConfigManagementService(repository=repository).prune_synced_disabled_mappings(
        path=config_path,
        artifact=_sync_artifact_with_services(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="fqdn-telegram",
                status=ServiceResultStatus.UPDATED,
            ),
            ServiceRunResult(
                service_key="youtube",
                object_group_name="fqdn-youtube",
                status=ServiceResultStatus.NO_CHANGES,
            ),
            ServiceRunResult(
                service_key="discord",
                object_group_name="fqdn-discord",
                status=ServiceResultStatus.FAILED,
            ),
        ),
    )

    assert [(mapping.router_id, mapping.service_key) for mapping in result.removed_mappings] == [
        ("router-1", "youtube")
    ]
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert [mapping["service_key"] for mapping in payload["mappings"]] == [
        "telegram",
        "discord",
    ]


def test_remove_router_removes_router_and_related_mappings(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config = AppConfig.model_validate(
        {
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_SECRET",
                },
                {
                    "id": "router-2",
                    "name": "Router 2",
                    "rci_url": "https://router-2.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_TWO_SECRET",
                },
            ],
            "services": [
                {
                    "key": "telegram",
                    "source_urls": ["https://example.com/telegram.lst"],
                    "format": "raw_domain_list",
                },
                {
                    "key": "youtube",
                    "source_urls": ["https://example.com/youtube.lst"],
                    "format": "raw_domain_list",
                },
            ],
            "mappings": [
                {
                    "router_id": "router-1",
                    "service_key": "telegram",
                    "object_group_name": "fqdn-telegram",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                },
                {
                    "router_id": "router-2",
                    "service_key": "youtube",
                    "object_group_name": "fqdn-youtube",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard1",
                },
            ],
        }
    )
    repository = ConfigRepository()
    repository.write_new(path=config_path, config=config)

    removed = ConfigManagementService(repository=repository).remove_router(
        path=config_path,
        router_id="router-1",
    )

    updated_config = repository.load(path=config_path)
    assert removed is True
    assert [router.id for router in updated_config.routers] == ["router-2"]
    assert [(mapping.router_id, mapping.service_key) for mapping in updated_config.mappings] == [
        ("router-2", "youtube")
    ]


def _sync_artifact_with_services(
    *service_results: ServiceRunResult,
) -> RunArtifact:
    timestamp = datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc)
    return RunArtifact(
        run_id="run-cleanup",
        trigger=RunTrigger.MANUAL,
        mode=RunMode.APPLY,
        status=RunStatus.SUCCESS,
        started_at=timestamp,
        finished_at=timestamp,
        log_path=Path("data/logs/run-cleanup.log"),
        router_results=[
            RouterRunResult(
                router_id="router-1",
                status=RouterResultStatus.UPDATED,
                service_results=service_results,
            )
        ],
    )
