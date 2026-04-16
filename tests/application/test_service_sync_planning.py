from __future__ import annotations

import pytest

from fqdn_updater.application.service_sync_planning import ServiceSyncPlanner
from fqdn_updater.domain.config_schema import RouterServiceMappingConfig
from fqdn_updater.domain.keenetic import ObjectGroupState, RouteBindingState
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.object_group_sharding import managed_shard_names
from fqdn_updater.domain.static_route_diff import StaticRouteState


def _mapping(*, managed: bool = True) -> RouterServiceMappingConfig:
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
            "managed": managed,
        }
    )


def test_service_sync_planner_builds_plan_for_managed_mapping() -> None:
    planner = ServiceSyncPlanner()
    actual_state = ObjectGroupState(
        name="svc-telegram",
        entries=["old.example", "keep.example"],
        exists=True,
    )

    plan = planner.plan(
        mapping=_mapping(),
        desired_entries=["keep.example", "new.example"],
        actual_state=actual_state,
        actual_route_binding=RouteBindingState(
            object_group_name="svc-telegram",
            exists=False,
        ),
    )

    assert plan.service_key == "telegram"
    assert plan.router_id == "router-1"
    assert plan.object_group_name == "svc-telegram"
    assert plan.object_group_diff.to_add == ("new.example",)
    assert plan.object_group_diff.to_remove == ("old.example",)
    assert plan.object_group_diff.unchanged == ("keep.example",)
    assert plan.desired_route_binding.object_group_name == "svc-telegram"
    assert plan.desired_route_binding.route_target_type == "gateway"
    assert plan.desired_route_binding.route_target_value == "10.0.0.1"
    assert plan.desired_route_binding.route_interface == "Wireguard0"
    assert plan.desired_route_binding.exclusive is True
    assert plan.desired_route_binding.auto is False
    assert plan.route_binding_diff.has_changes is True
    assert plan.has_changes is True


def test_service_sync_planner_rejects_unmanaged_mapping() -> None:
    planner = ServiceSyncPlanner()

    with pytest.raises(ValueError, match="must be managed"):
        planner.plan(
            mapping=_mapping(managed=False),
            desired_entries=["keep.example"],
            actual_state=ObjectGroupState(name="svc-telegram", entries=(), exists=False),
            actual_route_binding=RouteBindingState(
                object_group_name="svc-telegram",
                exists=False,
            ),
        )


def test_service_sync_planner_rejects_mismatched_object_group_name() -> None:
    planner = ServiceSyncPlanner()

    with pytest.raises(ValueError, match="does not match mapping"):
        planner.plan(
            mapping=_mapping(),
            desired_entries=["keep.example"],
            actual_state=ObjectGroupState(name="svc-other", entries=(), exists=False),
            actual_route_binding=RouteBindingState(
                object_group_name="svc-telegram",
                exists=False,
            ),
        )


def test_service_sync_planner_rejects_object_groups_above_keenetic_entry_limit() -> None:
    planner = ServiceSyncPlanner()
    desired_entries = [f"host-{index:03d}.example" for index in range(301)]

    with pytest.raises(ValueError, match="exceeding Keenetic FQDN object-group limit 300"):
        planner.plan(
            mapping=_mapping(),
            desired_entries=desired_entries,
            actual_state=ObjectGroupState(name="svc-telegram", entries=(), exists=False),
            actual_route_binding=RouteBindingState(
                object_group_name="svc-telegram",
                exists=False,
            ),
        )


def test_service_sync_planner_shards_large_mapping_plans() -> None:
    planner = ServiceSyncPlanner()
    desired_entries = [f"host-{index:03d}.example" for index in range(301)]

    plans = planner.plan_mapping(
        mapping=_mapping(),
        desired_entries=desired_entries,
        actual_states={
            name: ObjectGroupState(name=name, entries=(), exists=False)
            for name in managed_shard_names("svc-telegram")
        },
        actual_route_bindings={
            name: RouteBindingState(object_group_name=name, exists=False)
            for name in managed_shard_names("svc-telegram")
        },
    )

    assert [plan.object_group_name for plan in plans] == ["svc-telegram", "svc-telegram-2"]
    assert [len(plan.object_group_diff.to_add) for plan in plans] == [300, 1]
    assert plans[0].desired_route_binding is not None
    assert plans[0].desired_route_binding.object_group_name == "svc-telegram"
    assert plans[1].desired_route_binding is not None
    assert plans[1].desired_route_binding.object_group_name == "svc-telegram-2"
    assert [plan.remove_route for plan in plans] == [False, False]


def test_service_sync_planner_cleans_stale_shard_route() -> None:
    planner = ServiceSyncPlanner()

    plans = planner.plan_mapping(
        mapping=_mapping(),
        desired_entries=["keep.example"],
        actual_states={
            "svc-telegram": ObjectGroupState(
                name="svc-telegram",
                entries=("keep.example",),
                exists=True,
            ),
            "svc-telegram-2": ObjectGroupState(
                name="svc-telegram-2",
                entries=("old.example",),
                exists=True,
            ),
            "svc-telegram-3": ObjectGroupState(
                name="svc-telegram-3",
                entries=(),
                exists=False,
            ),
            "svc-telegram-4": ObjectGroupState(
                name="svc-telegram-4",
                entries=(),
                exists=False,
            ),
        },
        actual_route_bindings={
            "svc-telegram": RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="gateway",
                route_target_value="10.0.0.1",
                route_interface="Wireguard0",
                auto=False,
                exclusive=True,
            ),
            "svc-telegram-2": RouteBindingState(
                object_group_name="svc-telegram-2",
                exists=True,
                route_target_type="gateway",
                route_target_value="10.0.0.1",
                route_interface="Wireguard0",
                auto=False,
                exclusive=True,
            ),
            "svc-telegram-3": RouteBindingState(
                object_group_name="svc-telegram-3",
                exists=False,
            ),
            "svc-telegram-4": RouteBindingState(
                object_group_name="svc-telegram-4",
                exists=False,
            ),
        },
    )

    assert [plan.object_group_name for plan in plans] == ["svc-telegram", "svc-telegram-2"]
    assert plans[0].has_changes is False
    assert plans[1].object_group_diff.to_remove == ("old.example",)
    assert plans[1].desired_route_binding is None
    assert plans[1].route_binding_diff.desired_binding is None
    assert plans[1].remove_route is True
    assert plans[1].remove_object_group is True
    assert plans[1].has_changes is True


def test_service_sync_planner_cleans_empty_stale_shard_group() -> None:
    planner = ServiceSyncPlanner()

    plans = planner.plan_mapping(
        mapping=_mapping(),
        desired_entries=["keep.example"],
        actual_states={
            "svc-telegram": ObjectGroupState(
                name="svc-telegram",
                entries=("keep.example",),
                exists=True,
            ),
            "svc-telegram-2": ObjectGroupState(
                name="svc-telegram-2",
                entries=(),
                exists=True,
            ),
            "svc-telegram-3": ObjectGroupState(
                name="svc-telegram-3",
                entries=(),
                exists=False,
            ),
            "svc-telegram-4": ObjectGroupState(
                name="svc-telegram-4",
                entries=(),
                exists=False,
            ),
        },
        actual_route_bindings={
            "svc-telegram": RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="gateway",
                route_target_value="10.0.0.1",
                route_interface="Wireguard0",
                auto=False,
                exclusive=True,
            ),
            "svc-telegram-2": RouteBindingState(
                object_group_name="svc-telegram-2",
                exists=False,
            ),
            "svc-telegram-3": RouteBindingState(
                object_group_name="svc-telegram-3",
                exists=False,
            ),
            "svc-telegram-4": RouteBindingState(
                object_group_name="svc-telegram-4",
                exists=False,
            ),
        },
    )

    assert [plan.object_group_name for plan in plans] == ["svc-telegram", "svc-telegram-2"]
    assert plans[1].object_group_diff.has_changes is False
    assert plans[1].remove_route is False
    assert plans[1].remove_object_group is True
    assert plans[1].has_changes is True


def test_service_sync_planner_marks_route_only_changes() -> None:
    planner = ServiceSyncPlanner()

    plan = planner.plan(
        mapping=_mapping(),
        desired_entries=["keep.example"],
        actual_state=ObjectGroupState(
            name="svc-telegram",
            entries=["keep.example"],
            exists=True,
        ),
        actual_route_binding=RouteBindingState(
            object_group_name="svc-telegram",
            exists=True,
            route_target_type="gateway",
            route_target_value="10.0.0.1",
            route_interface="Wireguard0",
            auto=True,
            exclusive=False,
        ),
    )

    assert plan.object_group_diff.has_changes is False
    assert plan.route_binding_diff.has_changes is True
    assert plan.has_changes is True


def test_service_sync_planner_handles_mixed_typed_entries() -> None:
    planner = ServiceSyncPlanner()
    actual_state = ObjectGroupState(
        name="svc-telegram",
        typed_entries=(ObjectGroupEntry.from_domain("keep.example"),),
        exists=True,
    )

    plan = planner.plan(
        mapping=_mapping(),
        desired_entries=(
            ObjectGroupEntry.from_domain("keep.example"),
            ObjectGroupEntry.from_network("10.0.0.1/24"),
            ObjectGroupEntry.from_network("2001:db8::1/64"),
        ),
        actual_state=actual_state,
        actual_route_binding=RouteBindingState(
            object_group_name="svc-telegram",
            exists=False,
        ),
        actual_static_routes=(
            StaticRouteState(
                network="10.0.0.0/24",
                route_target_type="gateway",
                route_target_value="10.0.0.1",
                route_interface="Wireguard0",
                auto=False,
                exclusive=True,
                comment="fqdn-updater:telegram",
            ),
            StaticRouteState(
                network="2001:db8:1::/64",
                route_target_type="gateway",
                route_target_value="10.0.0.1",
                route_interface="Wireguard0",
                auto=False,
                exclusive=True,
                comment="fqdn-updater:telegram",
            ),
        ),
    )

    assert plan.object_group_diff.to_add == ()
    assert plan.object_group_diff.to_remove == ()
    assert plan.object_group_diff.typed_unchanged == (ObjectGroupEntry.from_domain("keep.example"),)
    assert [route.network for route in plan.static_route_diff.to_add] == ["2001:db8::/64"]
    assert [route.network for route in plan.static_route_diff.to_remove] == ["2001:db8:1::/64"]
    assert [route.network for route in plan.static_route_diff.unchanged] == ["10.0.0.0/24"]
    assert plan.has_changes is True


def test_service_sync_planner_skips_route_binding_for_subnet_only_services() -> None:
    planner = ServiceSyncPlanner()
    actual_state = ObjectGroupState(name="svc-telegram", entries=(), exists=False)

    plan = planner.plan(
        mapping=_mapping(),
        desired_entries=(ObjectGroupEntry.from_network("10.0.0.1/24"),),
        actual_state=actual_state,
        actual_route_binding=RouteBindingState(
            object_group_name="svc-telegram",
            exists=True,
            route_target_type="interface",
            route_target_value="Wireguard0",
            auto=True,
            exclusive=True,
        ),
        actual_static_routes=(),
    )

    assert plan.object_group_diff.has_changes is False
    assert plan.desired_route_binding is None
    assert plan.route_binding_diff.desired_binding is None
    assert plan.remove_route is True
    assert plan.static_route_diff.to_add[0].network == "10.0.0.0/24"
    assert plan.has_changes is True


def test_service_sync_planner_removes_stale_static_routes_when_subnets_disappear() -> None:
    planner = ServiceSyncPlanner()
    actual_state = ObjectGroupState(
        name="svc-telegram",
        entries=("keep.example",),
        exists=True,
    )

    plan = planner.plan(
        mapping=_mapping(),
        desired_entries=("keep.example",),
        actual_state=actual_state,
        actual_route_binding=RouteBindingState(
            object_group_name="svc-telegram",
            exists=True,
            route_target_type="gateway",
            route_target_value="10.0.0.1",
            route_interface="Wireguard0",
            auto=False,
            exclusive=True,
        ),
        actual_static_routes=(
            StaticRouteState(
                network="2001:db8:1::/64",
                route_target_type="gateway",
                route_target_value="10.0.0.1",
                route_interface="Wireguard0",
                auto=False,
                exclusive=True,
                comment="fqdn-updater:telegram",
            ),
        ),
    )

    assert plan.desired_route_binding is not None
    assert plan.route_binding_diff.has_changes is False
    assert plan.static_route_diff.to_remove == (
        StaticRouteState(
            network="2001:db8:1::/64",
            route_target_type="gateway",
            route_target_value="10.0.0.1",
            route_interface="Wireguard0",
            auto=False,
            exclusive=True,
            comment="fqdn-updater:telegram",
        ),
    )
    assert plan.static_route_diff.to_add == ()
    assert plan.has_changes is True
