from __future__ import annotations

import pytest
from pydantic import ValidationError

from fqdn_updater.domain.config_schema import AppConfig


def _router(**overrides: object) -> dict[str, object]:
    router = {
        "id": "router-1",
        "name": "Router 1",
        "rci_url": "https://router-1.example/rci/",
        "username": "api-user",
        "password_env": "ROUTER_SECRET",
        "enabled": True,
    }
    router.update(overrides)
    return router


def _service(**overrides: object) -> dict[str, object]:
    service = {
        "key": "telegram",
        "source_urls": ["https://example.com/telegram.lst"],
        "format": "raw_domain_list",
        "enabled": True,
    }
    service.update(overrides)
    return service


def _mapping(**overrides: object) -> dict[str, object]:
    mapping = {
        "router_id": "router-1",
        "service_key": "telegram",
        "object_group_name": "svc-telegram",
        "route_target_type": "interface",
        "route_target_value": "Wireguard0",
        "managed": True,
    }
    mapping.update(overrides)
    return mapping


def _config_payload(**overrides: object) -> dict[str, object]:
    payload = {
        "version": 1,
        "routers": [_router()],
        "services": [_service()],
        "mappings": [_mapping()],
        "runtime": {},
    }
    payload.update(overrides)
    return payload


def test_app_config_accepts_valid_runtime_ready_payload() -> None:
    config = AppConfig.model_validate(_config_payload())

    assert config.routers[0].id == "router-1"
    assert config.services[0].key == "telegram"
    assert config.mappings[0].object_group_name == "svc-telegram"
    assert config.mappings[0].auto is True
    assert config.mappings[0].exclusive is True
    assert config.runtime.secrets_env_file == ".env.secrets"
    assert str(config.services[0].resolved_sources[0].url) == "https://example.com/telegram.lst"
    assert config.services[0].resolved_sources[0].format == "raw_domain_list"


def test_service_definition_accepts_per_source_mixed_sources() -> None:
    payload = _config_payload(
        services=[
            {
                "key": "telegram",
                "sources": [
                    {
                        "url": "https://example.com/telegram.lst",
                        "format": "raw_domain_list",
                    },
                    {
                        "url": "https://example.com/telegram-v4.lst",
                        "format": "raw_cidr_list",
                    },
                    {
                        "url": "https://example.com/telegram-v6.lst",
                        "format": "raw_cidr_list",
                    },
                ],
                "enabled": True,
            }
        ]
    )

    config = AppConfig.model_validate(payload)

    assert [str(source.url) for source in config.services[0].resolved_sources] == [
        "https://example.com/telegram.lst",
        "https://example.com/telegram-v4.lst",
        "https://example.com/telegram-v6.lst",
    ]
    assert [source.format for source in config.services[0].resolved_sources] == [
        "raw_domain_list",
        "raw_cidr_list",
        "raw_cidr_list",
    ]


def test_service_definition_rejects_sources_and_source_urls_together() -> None:
    payload = _config_payload(
        services=[
            _service(
                sources=[
                    {
                        "url": "https://example.com/telegram.lst",
                        "format": "raw_domain_list",
                    }
                ]
            )
        ]
    )

    with pytest.raises(
        ValidationError,
        match="service must define either sources or source_urls/format, not both",
    ):
        AppConfig.model_validate(payload)


def test_service_definition_rejects_missing_source_definitions() -> None:
    payload = _config_payload(
        services=[
            {
                "key": "telegram",
                "enabled": True,
            }
        ]
    )

    with pytest.raises(ValidationError, match="source_urls must contain at least one URL"):
        AppConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("router_overrides", "message"),
    [
        ({"password_env": None}, "must define exactly one of password_env or password_file"),
        (
            {"password_file": "/run/secrets/router-1"},
            "must not define both password_env and password_file",
        ),
    ],
)
def test_enabled_router_requires_exactly_one_secret_reference(
    router_overrides: dict[str, object], message: str
) -> None:
    payload = _config_payload(routers=[_router(**router_overrides)])

    with pytest.raises(ValidationError, match=message):
        AppConfig.model_validate(payload)


@pytest.mark.parametrize(
    ("rci_url", "message"),
    [
        ("http://router-1.example/rci/", "rci_url must use https"),
        ("https://router-1.example/api/", "rci_url path must be /rci or /rci/"),
    ],
)
def test_router_rci_url_must_match_rci_contract(rci_url: str, message: str) -> None:
    payload = _config_payload(routers=[_router(rci_url=rci_url)])

    with pytest.raises(ValidationError, match=message):
        AppConfig.model_validate(payload)


def test_duplicate_router_ids_are_rejected() -> None:
    payload = _config_payload(
        routers=[
            _router(),
            _router(
                name="Router 2",
                rci_url="https://router-2.example/rci/",
                password_env=None,
                password_file="/run/secrets/router-2",
            ),
        ]
    )

    with pytest.raises(ValidationError, match="duplicate router id 'router-1'"):
        AppConfig.model_validate(payload)


def test_duplicate_service_keys_are_rejected() -> None:
    payload = _config_payload(
        services=[
            _service(),
            _service(
                source_urls=["https://example.com/telegram-alt.lst"],
            ),
        ]
    )

    with pytest.raises(ValidationError, match="duplicate service key 'telegram'"):
        AppConfig.model_validate(payload)


def test_mapping_references_must_exist() -> None:
    payload = _config_payload(
        mappings=[
            _mapping(router_id="missing-router", service_key="missing-service"),
        ]
    )

    with pytest.raises(ValidationError, match="references unknown router 'missing-router'"):
        AppConfig.model_validate(payload)


def test_duplicate_router_service_mapping_pair_is_rejected() -> None:
    payload = _config_payload(
        mappings=[
            _mapping(),
            _mapping(object_group_name="svc-telegram-2"),
        ]
    )

    with pytest.raises(ValidationError, match="duplicates router/service pair"):
        AppConfig.model_validate(payload)


def test_duplicate_managed_object_group_name_within_router_is_rejected() -> None:
    payload = _config_payload(
        services=[
            _service(),
            _service(
                key="youtube",
                source_urls=["https://example.com/youtube.lst"],
            ),
        ],
        mappings=[
            _mapping(),
            _mapping(service_key="youtube"),
        ],
    )

    with pytest.raises(ValidationError, match="reuses managed object_group_name 'svc-telegram'"):
        AppConfig.model_validate(payload)


def test_generated_shard_object_group_name_collision_within_router_is_rejected() -> None:
    payload = _config_payload(
        services=[
            _service(),
            _service(
                key="youtube",
                source_urls=["https://example.com/youtube.lst"],
            ),
        ],
        mappings=[
            _mapping(),
            _mapping(service_key="youtube", object_group_name="svc-telegram-2"),
        ],
    )

    with pytest.raises(
        ValidationError,
        match="reuses managed object_group_name 'svc-telegram-2'",
    ):
        AppConfig.model_validate(payload)


def test_route_interface_is_rejected_for_interface_target() -> None:
    payload = _config_payload(
        mappings=[
            _mapping(route_interface="Wireguard0"),
        ]
    )

    with pytest.raises(
        ValidationError, match="route_interface must be unset when route_target_type is interface"
    ):
        AppConfig.model_validate(payload)


def test_whitespace_only_critical_fields_are_rejected() -> None:
    payload = _config_payload(
        routers=[
            _router(id="   "),
        ]
    )

    with pytest.raises(ValidationError, match="id must not be empty"):
        AppConfig.model_validate(payload)


def test_service_key_must_use_safe_slug_format() -> None:
    payload = _config_payload(
        services=[
            _service(key="Telegram Main"),
        ]
    )

    with pytest.raises(
        ValidationError,
        match="key must use lowercase letters, digits, underscores, or hyphens",
    ):
        AppConfig.model_validate(payload)


def test_source_urls_must_not_be_empty() -> None:
    payload = _config_payload(
        services=[
            _service(source_urls=[]),
        ]
    )

    with pytest.raises(ValidationError, match="source_urls must contain at least one URL"):
        AppConfig.model_validate(payload)


def test_allowed_source_ips_must_be_valid_ip_or_cidr() -> None:
    payload = _config_payload(
        routers=[
            _router(allowed_source_ips=["not-an-ip"]),
        ]
    )

    with pytest.raises(
        ValidationError, match="allowed_source_ips item 'not-an-ip' must be a valid IP or CIDR"
    ):
        AppConfig.model_validate(payload)


def test_duplicate_router_tags_are_rejected() -> None:
    payload = _config_payload(
        routers=[
            _router(tags=["vps", "vps"]),
        ]
    )

    with pytest.raises(ValidationError, match="tags contains duplicate value 'vps'"):
        AppConfig.model_validate(payload)


def test_object_group_name_must_use_safe_slug_format() -> None:
    payload = _config_payload(
        mappings=[
            _mapping(object_group_name="Telegram Group"),
        ]
    )

    with pytest.raises(
        ValidationError,
        match="object_group_name must use lowercase letters, digits, underscores, or hyphens",
    ):
        AppConfig.model_validate(payload)
