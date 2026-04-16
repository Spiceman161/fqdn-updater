from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from fqdn_updater.domain.config_schema import RouterServiceMappingConfig
from fqdn_updater.domain.keenetic import (
    ObjectGroupState,
    RouteBindingDiff,
    RouteBindingSpec,
    RouteBindingState,
)
from fqdn_updater.domain.keenetic_limits import validate_fqdn_object_group_entry_count
from fqdn_updater.domain.object_group_diff import ObjectGroupDiff, build_object_group_diff
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.object_group_sharding import (
    managed_shard_names,
    shard_object_group_entries,
)
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
    desired_route_binding: RouteBindingSpec | None = None
    route_binding_diff: RouteBindingDiff
    remove_route: bool = False
    remove_object_group: bool = False

    @field_validator("service_key", "router_id", "object_group_name", mode="before")
    @classmethod
    def _validate_text_fields(cls, value: Any, info: Any) -> str:
        return _require_non_blank(str(value), info.field_name)

    @property
    def has_changes(self) -> bool:
        return (
            self.object_group_diff.has_changes
            or self.route_binding_diff.has_changes
            or self.remove_route
            or self.remove_object_group
        )


class ServiceSyncPlanner:
    def plan_mapping(
        self,
        mapping: RouterServiceMappingConfig,
        desired_entries: Iterable[str | ObjectGroupEntry],
        actual_states: Mapping[str, ObjectGroupState],
        actual_route_bindings: Mapping[str, RouteBindingState],
    ) -> tuple[ServiceSyncPlan, ...]:
        self._validate_mapping(mapping)

        desired_shards = shard_object_group_entries(
            base_name=mapping.object_group_name,
            entries=desired_entries,
        )
        desired_entries_by_group = {shard.name: shard.entries for shard in desired_shards}

        plans: list[ServiceSyncPlan] = []
        for index, object_group_name in enumerate(managed_shard_names(mapping.object_group_name)):
            actual_state = self._require_actual_state(
                object_group_name=object_group_name,
                actual_states=actual_states,
            )
            actual_route_binding = self._require_actual_route_binding(
                object_group_name=object_group_name,
                actual_route_bindings=actual_route_bindings,
            )
            shard_desired_entries = desired_entries_by_group.get(object_group_name, ())
            should_include_plan = (
                index == 0
                or bool(shard_desired_entries)
                or actual_state.exists
                or bool(actual_state.typed_entries)
                or actual_route_binding.exists
            )
            if not should_include_plan:
                continue

            plans.append(
                self._build_plan(
                    mapping=mapping,
                    object_group_name=object_group_name,
                    desired_entries=shard_desired_entries,
                    actual_state=actual_state,
                    actual_route_binding=actual_route_binding,
                    ensure_route=bool(shard_desired_entries),
                    remove_object_group=index > 0
                    and not shard_desired_entries
                    and actual_state.exists,
                )
            )

        return tuple(plans)

    def plan(
        self,
        mapping: RouterServiceMappingConfig,
        desired_entries: Iterable[str | ObjectGroupEntry],
        actual_state: ObjectGroupState,
        actual_route_binding: RouteBindingState,
    ) -> ServiceSyncPlan:
        self._validate_mapping(mapping)
        return self._build_plan(
            mapping=mapping,
            object_group_name=mapping.object_group_name,
            desired_entries=desired_entries,
            actual_state=actual_state,
            actual_route_binding=actual_route_binding,
            ensure_route=True,
            remove_object_group=False,
        )

    def _validate_mapping(self, mapping: RouterServiceMappingConfig) -> None:
        if not mapping.managed:
            raise ValueError(
                f"mapping for router '{mapping.router_id}' and service '{mapping.service_key}' "
                "must be managed"
            )

    def _build_plan(
        self,
        *,
        mapping: RouterServiceMappingConfig,
        object_group_name: str,
        desired_entries: Iterable[str | ObjectGroupEntry],
        actual_state: ObjectGroupState,
        actual_route_binding: RouteBindingState,
        ensure_route: bool,
        remove_object_group: bool,
    ) -> ServiceSyncPlan:
        if actual_state.name != object_group_name:
            raise ValueError(
                f"actual object-group '{actual_state.name}' does not match mapping "
                f"'{object_group_name}'"
            )
        if actual_route_binding.object_group_name != object_group_name:
            raise ValueError(
                f"actual route binding '{actual_route_binding.object_group_name}' does not "
                f"match mapping '{object_group_name}'"
            )

        object_group_diff = build_object_group_diff(
            desired_entries=desired_entries,
            actual_state=actual_state,
        )
        validate_fqdn_object_group_entry_count(
            object_group_name=object_group_name,
            entry_count=len(object_group_diff.typed_to_add)
            + len(object_group_diff.typed_unchanged),
        )
        desired_route_binding = (
            RouteBindingSpec(
                object_group_name=object_group_name,
                route_target_type=mapping.route_target_type,
                route_target_value=mapping.route_target_value,
                route_interface=mapping.route_interface,
                auto=mapping.auto,
                exclusive=mapping.exclusive,
            )
            if ensure_route
            else None
        )
        route_binding_diff = (
            build_route_binding_diff(
                actual_state=actual_route_binding,
                desired_binding=desired_route_binding,
            )
            if desired_route_binding is not None
            else RouteBindingDiff(
                object_group_name=object_group_name,
                current_binding=actual_route_binding,
                desired_binding=None,
                has_changes=actual_route_binding.exists,
            )
        )

        return ServiceSyncPlan(
            service_key=mapping.service_key,
            router_id=mapping.router_id,
            object_group_name=object_group_name,
            object_group_diff=object_group_diff,
            desired_route_binding=desired_route_binding,
            route_binding_diff=route_binding_diff,
            remove_route=desired_route_binding is None and actual_route_binding.exists,
            remove_object_group=remove_object_group,
        )

    def _require_actual_state(
        self,
        *,
        object_group_name: str,
        actual_states: Mapping[str, ObjectGroupState],
    ) -> ObjectGroupState:
        actual_state = actual_states.get(object_group_name)
        if actual_state is None:
            raise ValueError(f"missing actual object-group state for '{object_group_name}'")
        return actual_state

    def _require_actual_route_binding(
        self,
        *,
        object_group_name: str,
        actual_route_bindings: Mapping[str, RouteBindingState],
    ) -> RouteBindingState:
        actual_route_binding = actual_route_bindings.get(object_group_name)
        if actual_route_binding is None:
            raise ValueError(f"missing actual route binding state for '{object_group_name}'")
        return actual_route_binding
