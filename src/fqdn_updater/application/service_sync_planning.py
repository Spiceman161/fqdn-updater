from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from fqdn_updater.domain.config_schema import RouterServiceMappingConfig
from fqdn_updater.domain.keenetic import (
    ObjectGroupState,
    RouteBindingDiff,
    RouteBindingSpec,
    RouteBindingState,
)
from fqdn_updater.domain.object_group_diff import ObjectGroupDiff, build_object_group_diff
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.route_binding_diff import build_route_binding_diff


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


class ServiceSyncPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_key: str
    router_id: str
    object_group_name: str
    object_group_diff: ObjectGroupDiff
    desired_route_binding: RouteBindingSpec
    route_binding_diff: RouteBindingDiff

    @field_validator("service_key", "router_id", "object_group_name", mode="before")
    @classmethod
    def _validate_text_fields(cls, value: Any, info: Any) -> str:
        return _require_non_blank(str(value), info.field_name)

    @property
    def has_changes(self) -> bool:
        return self.object_group_diff.has_changes or self.route_binding_diff.has_changes


class ServiceSyncPlanner:
    def plan(
        self,
        mapping: RouterServiceMappingConfig,
        desired_entries: Iterable[str | ObjectGroupEntry],
        actual_state: ObjectGroupState,
        actual_route_binding: RouteBindingState,
    ) -> ServiceSyncPlan:
        if not mapping.managed:
            raise ValueError(
                f"mapping for router '{mapping.router_id}' and service '{mapping.service_key}' "
                "must be managed"
            )
        if actual_state.name != mapping.object_group_name:
            raise ValueError(
                f"actual object-group '{actual_state.name}' does not match mapping "
                f"'{mapping.object_group_name}'"
            )

        object_group_diff = build_object_group_diff(
            desired_entries=desired_entries,
            actual_state=actual_state,
        )
        desired_route_binding = RouteBindingSpec(
            object_group_name=mapping.object_group_name,
            route_target_type=mapping.route_target_type,
            route_target_value=mapping.route_target_value,
            route_interface=mapping.route_interface,
            auto=mapping.auto,
            exclusive=mapping.exclusive,
        )

        return ServiceSyncPlan(
            service_key=mapping.service_key,
            router_id=mapping.router_id,
            object_group_name=mapping.object_group_name,
            object_group_diff=object_group_diff,
            desired_route_binding=desired_route_binding,
            route_binding_diff=build_route_binding_diff(
                actual_state=actual_route_binding,
                desired_binding=desired_route_binding,
            ),
        )
