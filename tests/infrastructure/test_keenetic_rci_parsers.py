from __future__ import annotations

import pytest

from fqdn_updater.domain.keenetic import RouteTargetCandidate
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.static_route_diff import StaticRouteState
from fqdn_updater.infrastructure.keenetic_rci_parsers import (
    parse_dns_proxy_status,
    parse_object_group_state,
    parse_route_binding_state,
    parse_static_routes,
    parse_wireguard_route_target_candidates,
    unwrap_response_path,
)


def _runtime_error(operation: str, message: str) -> RuntimeError:
    return RuntimeError(f"Router 'router-1' {operation} failed: {message}")


def test_unwrap_response_path_preserves_client_error_shape() -> None:
    with pytest.raises(
        RuntimeError,
        match=(
            r"Router 'router-1' get_object_group\(svc-telegram\) failed: "
            r"response is missing 'fqdn'"
        ),
    ):
        unwrap_response_path(
            [{"show": {"sc": {"object-group": {}}}}],
            operation="get_object_group(svc-telegram)",
            path=("show", "sc", "object-group", "fqdn"),
            runtime_error=_runtime_error,
        )


def test_parse_object_group_state_preserves_cli_style_behavior() -> None:
    state = parse_object_group_state(
        groups_payload={
            "group": [
                {
                    "group-name": "svc-other",
                    "entry": [{"type": "config", "fqdn": "ignore.example"}],
                },
                {
                    "group-name": "svc-telegram",
                    "entry": [
                        {"type": "runtime", "fqdn": "runtime.example"},
                        {"type": "config", "address": "2001:db8::1/64"},
                        {"type": "config", "fqdn": "B.Example.com."},
                        {"type": "config", "address": "10.0.0.1/24"},
                    ],
                },
            ]
        },
        name="svc-telegram",
        runtime_error=_runtime_error,
    )

    assert state.entries == ("10.0.0.1/24", "2001:db8::1/64", "B.Example.com.")
    assert state.typed_entries == (
        ObjectGroupEntry.from_domain("b.example.com"),
        ObjectGroupEntry.from_network("10.0.0.0/24"),
        ObjectGroupEntry.from_network("2001:db8::/64"),
    )


def test_parse_route_binding_state_preserves_gateway_shape() -> None:
    state = parse_route_binding_state(
        dns_proxy_payload={
            "route": {
                "object-group": {
                    "svc-telegram": {
                        "type": "gateway",
                        "target": "10.1.111.12",
                        "interface": "Wireguard0",
                        "auto": True,
                        "reject": True,
                    }
                }
            }
        },
        object_group_name="svc-telegram",
        runtime_error=_runtime_error,
    )

    assert state.exists is True
    assert state.route_target_type == "gateway"
    assert state.route_target_value == "10.1.111.12"
    assert state.route_interface == "Wireguard0"
    assert state.auto is True
    assert state.exclusive is True


def test_parse_static_routes_preserves_comments_and_sorting() -> None:
    routes = parse_static_routes(
        route_payload=[
            {
                "network": "203.0.113.0",
                "mask": "255.255.255.0",
                "interface": "ISP",
            },
            {
                "network": "149.154.160.0",
                "mask": "255.255.240.0",
                "interface": "Wireguard0",
                "auto": True,
                "reject": True,
                "comment": "fqdn-updater:telegram Telegram",
            },
        ],
        operation="get_static_routes(ip)",
        runtime_error=_runtime_error,
    )

    assert routes == (
        StaticRouteState(
            network="149.154.160.0/20",
            route_target_type="interface",
            route_target_value="Wireguard0",
            auto=True,
            exclusive=True,
            comment="fqdn-updater:telegram Telegram",
        ),
        StaticRouteState(
            network="203.0.113.0/24",
            route_target_type="interface",
            route_target_value="ISP",
        ),
    )


def test_parse_dns_proxy_status_preserves_boolean_like_values() -> None:
    assert (
        parse_dns_proxy_status(
            dns_proxy_payload={"proxy-status": "enabled"},
            runtime_error=_runtime_error,
        ).enabled
        is True
    )
    assert (
        parse_dns_proxy_status(
            dns_proxy_payload={"enable": "off"},
            runtime_error=_runtime_error,
        ).enabled
        is False
    )


def test_parse_wireguard_route_target_candidates_preserves_discovery_behavior() -> None:
    candidates = parse_wireguard_route_target_candidates(
        {
            "Wireguard2": {
                "id": "Wireguard2",
                "type": "Wireguard",
                "description": "Backup tunnel",
                "interface-name": "Wireguard2",
                "connected": "no",
                "state": "down",
            },
            "Ethernet0": {
                "id": "Ethernet0",
                "type": "ethernet",
                "description": "Uplink",
                "interface-name": "Ethernet0",
                "connected": "yes",
                "state": "up",
            },
            "Wireguard0": {
                "id": "Wireguard0",
                "type": "Wireguard",
                "description": "Primary tunnel",
                "interface-name": "Wireguard0",
                "connected": True,
                "state": "connected",
            },
        }
    )

    assert candidates == (
        RouteTargetCandidate(
            value="Wireguard0",
            display_name="Wireguard0",
            status="connected",
            detail="type=Wireguard, Primary tunnel",
            connected=True,
        ),
        RouteTargetCandidate(
            value="Wireguard2",
            display_name="Wireguard2",
            status="down",
            detail="type=Wireguard, Backup tunnel",
            connected=False,
        ),
    )
