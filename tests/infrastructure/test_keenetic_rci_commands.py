from __future__ import annotations

import pytest

from fqdn_updater.domain.keenetic import RouteBindingSpec, RouteBindingState
from fqdn_updater.domain.static_route_diff import StaticRouteSpec, StaticRouteState
from fqdn_updater.infrastructure.keenetic_rci_commands import (
    build_add_entry_command,
    build_ensure_object_group_command,
    build_ensure_route_command,
    build_ensure_static_route_command,
    build_remove_entry_command,
    build_remove_object_group_command,
    build_remove_route_command,
    build_remove_static_route_command,
    build_save_config_command,
)


def test_object_group_commands_match_rci_payloads() -> None:
    assert build_ensure_object_group_command("svc-telegram") == {
        "parse": "object-group fqdn svc-telegram"
    }
    assert build_remove_object_group_command("svc-telegram") == {
        "parse": "no object-group fqdn svc-telegram"
    }
    assert build_add_entry_command("svc-telegram", "a.example") == {
        "parse": "object-group fqdn svc-telegram include a.example"
    }
    assert build_remove_entry_command("svc-telegram", "a.example") == {
        "parse": "no object-group fqdn svc-telegram include a.example"
    }


def test_route_binding_commands_match_rci_payloads() -> None:
    assert build_ensure_route_command(
        RouteBindingSpec(
            object_group_name="svc-telegram",
            route_target_type="gateway",
            route_target_value="10.1.111.12",
            route_interface="Wireguard0",
            auto=True,
            exclusive=True,
        )
    ) == {"parse": "dns-proxy route object-group svc-telegram 10.1.111.12 Wireguard0 auto reject"}
    assert build_remove_route_command(
        RouteBindingState(
            object_group_name="svc-telegram",
            exists=True,
            route_target_type="interface",
            route_target_value="Wireguard0",
        )
    ) == {"parse": "no dns-proxy route object-group svc-telegram Wireguard0"}


def test_static_route_commands_match_rci_payloads() -> None:
    assert build_ensure_static_route_command(
        StaticRouteSpec(
            service_key="telegram",
            network="149.154.160.0/20",
            route_target_type="gateway",
            route_target_value="10.1.111.12",
            route_interface="Wireguard0",
            auto=True,
            exclusive=True,
            comment="fqdn-updater:telegram Telegram",
        )
    ) == {
        "ip": {
            "route": {
                "network": "149.154.160.0",
                "mask": "255.255.240.0",
                "gateway": "10.1.111.12",
                "interface": "Wireguard0",
                "comment": "fqdn-updater:telegram Telegram",
                "auto": True,
                "reject": True,
            }
        }
    }
    assert build_remove_static_route_command(
        StaticRouteState(
            network="2001:67c:4e8::/48",
            route_target_type="interface",
            route_target_value="Wireguard0",
            comment="fqdn-updater:telegram Telegram",
        )
    ) == {
        "ipv6": {
            "route": {
                "prefix": "2001:67c:4e8::/48",
                "interface": "Wireguard0",
                "comment": "fqdn-updater:telegram Telegram",
                "no": True,
            }
        }
    }


def test_save_config_command_matches_rci_payload() -> None:
    assert build_save_config_command() == {"parse": "system configuration save"}


def test_cli_commands_reject_whitespace_arguments() -> None:
    with pytest.raises(ValueError, match="name must not contain whitespace"):
        build_ensure_object_group_command("svc telegram")
