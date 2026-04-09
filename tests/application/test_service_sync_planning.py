from __future__ import annotations

import pytest

from fqdn_updater.application.service_sync_planning import ServiceSyncPlanner
from fqdn_updater.domain.config_schema import RouterServiceMappingConfig
from fqdn_updater.domain.keenetic import ObjectGroupState, RouteBindingState


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
