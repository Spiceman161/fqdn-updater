from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from fqdn_updater.domain.keenetic import RouterInterfaceState

DEFAULT_ROUTE_HIGHEST_PRIORITY = 65534


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


class DefaultRoutePriorityChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interface: str
    priority: int = Field(ge=1, le=DEFAULT_ROUTE_HIGHEST_PRIORITY)

    @field_validator("interface", mode="before")
    @classmethod
    def _validate_interface(cls, value: Any) -> str:
        return _require_non_blank(str(value), "interface")


class DefaultRoutePlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    router_id: str
    desired_interface: str
    actual_interfaces: tuple[RouterInterfaceState, ...] = Field(default_factory=tuple)
    priority_changes: tuple[DefaultRoutePriorityChange, ...] = Field(default_factory=tuple)

    @field_validator("router_id", "desired_interface", mode="before")
    @classmethod
    def _validate_text_fields(cls, value: Any, info: Any) -> str:
        return _require_non_blank(str(value), info.field_name)

    @property
    def has_changes(self) -> bool:
        return bool(self.priority_changes)


def build_default_route_plan(
    *,
    router_id: str,
    desired_interface: str,
    actual_interfaces: tuple[RouterInterfaceState, ...],
) -> DefaultRoutePlan:
    normalized_desired_interface = _require_non_blank(desired_interface, "desired_interface")
    interfaces_by_value = {interface.value: interface for interface in actual_interfaces}
    selected_interface = interfaces_by_value.get(normalized_desired_interface)
    if selected_interface is None:
        raise ValueError(
            f"default route interface '{normalized_desired_interface}' was not found in router "
            "interface state"
        )

    global_interfaces = tuple(
        interface for interface in actual_interfaces if interface.global_priority is not None
    )
    if not global_interfaces:
        return DefaultRoutePlan(
            router_id=router_id,
            desired_interface=normalized_desired_interface,
            actual_interfaces=actual_interfaces,
            priority_changes=(
                DefaultRoutePriorityChange(
                    interface=normalized_desired_interface,
                    priority=DEFAULT_ROUTE_HIGHEST_PRIORITY,
                ),
            ),
        )

    highest_priority = max(interface.global_priority or 0 for interface in global_interfaces)
    highest_interfaces = tuple(
        interface
        for interface in global_interfaces
        if interface.global_priority == highest_priority
    )
    if (
        selected_interface.global_priority == highest_priority
        and len(highest_interfaces) == 1
        and highest_interfaces[0].value == normalized_desired_interface
    ):
        return DefaultRoutePlan(
            router_id=router_id,
            desired_interface=normalized_desired_interface,
            actual_interfaces=actual_interfaces,
        )

    changes: list[DefaultRoutePriorityChange] = []
    if selected_interface.global_priority != DEFAULT_ROUTE_HIGHEST_PRIORITY:
        changes.append(
            DefaultRoutePriorityChange(
                interface=normalized_desired_interface,
                priority=DEFAULT_ROUTE_HIGHEST_PRIORITY,
            )
        )

    next_lower_priority = DEFAULT_ROUTE_HIGHEST_PRIORITY - 1
    for interface in sorted(global_interfaces, key=lambda item: item.value.lower()):
        if interface.value == normalized_desired_interface:
            continue
        if interface.global_priority != DEFAULT_ROUTE_HIGHEST_PRIORITY:
            continue
        changes.append(
            DefaultRoutePriorityChange(
                interface=interface.value,
                priority=next_lower_priority,
            )
        )
        next_lower_priority -= 1

    return DefaultRoutePlan(
        router_id=router_id,
        desired_interface=normalized_desired_interface,
        actual_interfaces=actual_interfaces,
        priority_changes=tuple(changes),
    )
