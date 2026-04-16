from __future__ import annotations

from fqdn_updater.domain.config_schema import RouterServiceMappingConfig
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.static_route_diff import (
    MANAGED_STATIC_ROUTE_COMMENT_PREFIX,
    StaticRouteSpec,
    StaticRouteState,
    build_static_route_diff,
    build_static_route_specs,
    is_service_static_route_comment,
    managed_static_route_comment,
    service_static_route_comment_prefix,
)


def _mapping() -> RouterServiceMappingConfig:
    return RouterServiceMappingConfig.model_validate(
        {
            "router_id": "router-1",
            "service_key": "telegram",
            "object_group_name": "svc-telegram",
            "route_target_type": "gateway",
            "route_target_value": "10.0.0.1",
            "route_interface": "Wireguard0",
            "exclusive": True,
            "auto": False,
            "managed": True,
        }
    )


def test_managed_static_route_comment_and_prefix_helpers_use_service_identity() -> None:
    comment = managed_static_route_comment(service_key="telegram", display_name="Telegram")

    assert comment == "fqdn-updater:telegram"
    assert (
        managed_static_route_comment(service_key="meta", display_name="Meta Platforms")
        == "fqdn-updater:meta Meta Platforms"
    )
    assert service_static_route_comment_prefix("telegram") == "fqdn-updater:telegram"
    assert is_service_static_route_comment(comment=comment, service_key="telegram") is True
    assert (
        is_service_static_route_comment(
            comment="fqdn-updater:youtube YouTube", service_key="telegram"
        )
        is False
    )
    assert MANAGED_STATIC_ROUTE_COMMENT_PREFIX == "fqdn-updater:"


def test_build_static_route_specs_normalizes_ipv4_and_ipv6_entries() -> None:
    specs = build_static_route_specs(
        mapping=_mapping(),
        desired_entries=(
            ObjectGroupEntry.from_domain("keep.example"),
            ObjectGroupEntry.from_network("10.0.0.1/24"),
            ObjectGroupEntry.from_network("2001:db8::1/64"),
        ),
        service_display_name="Telegram",
    )

    assert specs == (
        StaticRouteSpec(
            service_key="telegram",
            network="10.0.0.0/24",
            route_target_type="gateway",
            route_target_value="10.0.0.1",
            route_interface="Wireguard0",
            auto=False,
            exclusive=True,
            comment="fqdn-updater:telegram",
        ),
        StaticRouteSpec(
            service_key="telegram",
            network="2001:db8::/64",
            route_target_type="gateway",
            route_target_value="10.0.0.1",
            route_interface="Wireguard0",
            auto=False,
            exclusive=True,
            comment="fqdn-updater:telegram",
        ),
    )
    assert specs[0].netmask == "255.255.255.0"
    assert specs[1].prefixlen == 64


def test_build_static_route_diff_reports_unchanged_add_and_remove_for_managed_routes() -> None:
    desired_routes = (
        StaticRouteSpec(
            service_key="telegram",
            network="149.154.160.0/20",
            route_target_type="interface",
            route_target_value="Wireguard0",
            comment="fqdn-updater:telegram Telegram",
        ),
        StaticRouteSpec(
            service_key="telegram",
            network="2001:67c:4e8::/48",
            route_target_type="interface",
            route_target_value="Wireguard0",
            comment="fqdn-updater:telegram Telegram",
        ),
    )
    actual_routes = (
        StaticRouteState(
            network="149.154.160.0/20",
            route_target_type="interface",
            route_target_value="Wireguard0",
            auto=True,
            comment="fqdn-updater:telegram Telegram",
        ),
        StaticRouteState(
            network="2001:67c:4e9::/48",
            route_target_type="interface",
            route_target_value="Wireguard0",
            comment="fqdn-updater:telegram Telegram",
        ),
        StaticRouteState(
            network="149.154.176.0/20",
            route_target_type="interface",
            route_target_value="Wireguard0",
            comment="fqdn-updater:youtube YouTube",
        ),
    )

    diff = build_static_route_diff(
        service_key="telegram",
        desired_routes=desired_routes,
        actual_routes=actual_routes,
    )

    assert diff.to_add == (desired_routes[1],)
    assert diff.to_remove == (
        StaticRouteState(
            network="2001:67c:4e9::/48",
            route_target_type="interface",
            route_target_value="Wireguard0",
            comment="fqdn-updater:telegram Telegram",
        ),
    )
    assert diff.unchanged == (
        StaticRouteState(
            network="149.154.160.0/20",
            route_target_type="interface",
            route_target_value="Wireguard0",
            auto=True,
            comment="fqdn-updater:telegram Telegram",
        ),
    )
    assert diff.has_changes is True


def test_build_static_route_diff_ignores_routes_without_matching_service_comment() -> None:
    diff = build_static_route_diff(
        service_key="telegram",
        desired_routes=(),
        actual_routes=(
            StaticRouteState(
                network="149.154.160.0/20",
                route_target_type="interface",
                route_target_value="Wireguard0",
                comment="fqdn-updater:telegram Telegram",
            ),
            StaticRouteState(
                network="149.154.176.0/20",
                route_target_type="interface",
                route_target_value="Wireguard0",
                comment="fqdn-updater:youtube YouTube",
            ),
            StaticRouteState(
                network="149.154.160.0/20",
                route_target_type="interface",
                route_target_value="Wireguard0",
                comment=None,
            ),
        ),
    )

    assert diff.to_add == ()
    assert diff.to_remove == (
        StaticRouteState(
            network="149.154.160.0/20",
            route_target_type="interface",
            route_target_value="Wireguard0",
            comment="fqdn-updater:telegram Telegram",
        ),
    )
    assert diff.unchanged == ()
    assert diff.has_changes is True


def test_build_static_route_diff_rejects_overlapping_unmanaged_routes() -> None:
    desired_route = StaticRouteSpec(
        service_key="telegram",
        network="149.154.160.0/20",
        route_target_type="interface",
        route_target_value="Wireguard0",
        comment="fqdn-updater:telegram Telegram",
    )

    try:
        build_static_route_diff(
            service_key="telegram",
            desired_routes=(desired_route,),
            actual_routes=(
                StaticRouteState(
                    network="149.154.160.0/20",
                    route_target_type="interface",
                    route_target_value="Wireguard0",
                    comment=None,
                ),
            ),
        )
    except ValueError as exc:
        assert "overlaps an unmanaged or differently managed route" in str(exc)
    else:
        raise AssertionError("expected overlapping unmanaged route to be rejected")
