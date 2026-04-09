from __future__ import annotations

from fqdn_updater.domain.keenetic import RouteBindingSpec, RouteBindingState
from fqdn_updater.domain.route_binding_diff import build_route_binding_diff


def test_build_route_binding_diff_reports_no_changes_for_matching_binding() -> None:
    desired_binding = RouteBindingSpec(
        object_group_name="svc-telegram",
        route_target_type="gateway",
        route_target_value="10.0.0.1",
        route_interface="Wireguard0",
        auto=True,
        exclusive=True,
    )

    diff = build_route_binding_diff(
        actual_state=RouteBindingState(
            object_group_name="svc-telegram",
            exists=True,
            route_target_type="gateway",
            route_target_value="10.0.0.1",
            route_interface="Wireguard0",
            auto=True,
            exclusive=True,
        ),
        desired_binding=desired_binding,
    )

    assert diff.has_changes is False


def test_build_route_binding_diff_reports_changes_for_missing_binding() -> None:
    diff = build_route_binding_diff(
        actual_state=RouteBindingState(
            object_group_name="svc-telegram",
            exists=False,
        ),
        desired_binding=RouteBindingSpec(
            object_group_name="svc-telegram",
            route_target_type="interface",
            route_target_value="Wireguard0",
        ),
    )

    assert diff.has_changes is True


def test_build_route_binding_diff_reports_changes_for_flag_updates() -> None:
    diff = build_route_binding_diff(
        actual_state=RouteBindingState(
            object_group_name="svc-telegram",
            exists=True,
            route_target_type="interface",
            route_target_value="Wireguard0",
            auto=False,
            exclusive=False,
        ),
        desired_binding=RouteBindingSpec(
            object_group_name="svc-telegram",
            route_target_type="interface",
            route_target_value="Wireguard0",
            auto=True,
            exclusive=True,
        ),
    )

    assert diff.has_changes is True
