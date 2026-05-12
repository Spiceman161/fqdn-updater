from __future__ import annotations

import pytest

from fqdn_updater.domain.default_route import build_default_route_plan
from fqdn_updater.domain.keenetic import RouterInterfaceState


def test_default_route_plan_noops_when_selected_interface_is_unique_highest_priority() -> None:
    plan = build_default_route_plan(
        router_id="router-1",
        desired_interface="Wireguard0",
        actual_interfaces=(
            RouterInterfaceState(value="Provider0", global_priority=100),
            RouterInterfaceState(value="Wireguard0", global_priority=200),
        ),
    )

    assert plan.has_changes is False
    assert plan.priority_changes == ()


def test_default_route_plan_sets_selected_highest_and_lowers_only_conflicting_highest() -> None:
    plan = build_default_route_plan(
        router_id="router-1",
        desired_interface="Wireguard0",
        actual_interfaces=(
            RouterInterfaceState(value="Provider0", global_priority=65534),
            RouterInterfaceState(value="Backup0", global_priority=65534),
            RouterInterfaceState(value="Other0", global_priority=100),
            RouterInterfaceState(value="Wireguard0", global_priority=50000),
        ),
    )

    assert [(change.interface, change.priority) for change in plan.priority_changes] == [
        ("Wireguard0", 65534),
        ("Backup0", 65533),
        ("Provider0", 65532),
    ]


def test_default_route_plan_rejects_missing_selected_interface() -> None:
    with pytest.raises(ValueError, match="was not found"):
        build_default_route_plan(
            router_id="router-1",
            desired_interface="Wireguard0",
            actual_interfaces=(RouterInterfaceState(value="Provider0", global_priority=65534),),
        )
