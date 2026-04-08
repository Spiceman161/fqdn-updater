from __future__ import annotations

import pytest

from fqdn_updater.infrastructure.keenetic_rci_client import (
    KeeneticRciClient,
    KeeneticRciClientFactory,
    RciConnectionProfile,
)


def test_connection_profile_builds_from_router_config(router_config) -> None:
    profile = RciConnectionProfile.from_router_config(router=router_config, password="secret")

    assert profile.router_id == "router-1"
    assert profile.endpoint_url == "https://router-1.example/rci/"
    assert profile.username == "api-user"
    assert profile.password == "secret"
    assert profile.timeout_seconds == 15


def test_factory_creates_rci_client_with_profile(router_config) -> None:
    client = KeeneticRciClientFactory().create(router=router_config, password="secret")

    assert isinstance(client, KeeneticRciClient)
    assert client.profile.router_id == "router-1"


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("get_object_group", ("svc-telegram",)),
        ("ensure_object_group", ("svc-telegram",)),
        ("add_entries", ("svc-telegram", ["a.example"])),
        ("remove_entries", ("svc-telegram", ["a.example"])),
        ("ensure_route", ({"object_group_name": "svc-telegram"},)),
        ("save_config", ()),
        ("get_dns_proxy_status", ()),
    ],
)
def test_client_methods_are_explicitly_unimplemented(
    router_config, method_name: str, args: tuple[object, ...]
) -> None:
    client = KeeneticRciClientFactory().create(router=router_config, password="secret")
    method = getattr(client, method_name)

    with pytest.raises(NotImplementedError, match=rf"KeeneticRciClient\.{method_name}"):
        method(*args)


@pytest.fixture
def router_config():
    from fqdn_updater.domain.config_schema import RouterConfig

    return RouterConfig.model_validate(
        {
            "id": "router-1",
            "name": "Router 1",
            "rci_url": "https://router-1.example/rci/",
            "username": "api-user",
            "password_env": "ROUTER_ONE_PASSWORD",
            "timeout_seconds": 15,
            "enabled": True,
        }
    )
