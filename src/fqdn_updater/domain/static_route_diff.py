from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from fqdn_updater.domain.config_schema import RouterServiceMappingConfig
from fqdn_updater.domain.object_group_entry import (
    ObjectGroupEntry,
    normalize_object_group_entries,
)

MANAGED_STATIC_ROUTE_COMMENT_PREFIX = "fqdn-updater:"


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


def managed_static_route_comment(
    *,
    service_key: str,
    display_name: str | None = None,
) -> str:
    normalized_service_key = _require_non_blank(service_key, "service_key")
    prefix = f"{MANAGED_STATIC_ROUTE_COMMENT_PREFIX}{normalized_service_key}"
    if display_name is None:
        return prefix

    normalized_display_name = _require_non_blank(display_name, "display_name")
    if normalized_display_name.casefold() == normalized_service_key.casefold():
        return prefix
    return f"{prefix} {normalized_display_name}"


def service_static_route_comment_prefix(service_key: str) -> str:
    return f"{MANAGED_STATIC_ROUTE_COMMENT_PREFIX}{_require_non_blank(service_key, 'service_key')}"


def is_service_static_route_comment(*, comment: str | None, service_key: str) -> bool:
    if comment is None:
        return False
    prefix = service_static_route_comment_prefix(service_key)
    return comment == prefix or comment.startswith(f"{prefix} ")


class StaticRouteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_key: str
    network: str
    route_target_type: Literal["interface", "gateway"]
    route_target_value: str
    route_interface: str | None = None
    auto: bool = True
    exclusive: bool = False
    comment: str

    @field_validator(
        "service_key",
        "route_target_value",
        "route_interface",
        "comment",
        mode="before",
    )
    @classmethod
    def _validate_text_fields(cls, value: Any, info: Any) -> str | None:
        if value is None:
            return None
        return _require_non_blank(str(value), info.field_name)

    @field_validator("network", mode="before")
    @classmethod
    def _validate_network(cls, value: Any) -> str:
        return str(ipaddress.ip_network(str(value), strict=False))

    @model_validator(mode="after")
    def _validate_target_shape(self) -> StaticRouteSpec:
        if self.route_target_type == "interface" and self.route_interface is not None:
            raise ValueError("route_interface must be unset when route_target_type is interface")
        return self

    @property
    def version(self) -> int:
        return ipaddress.ip_network(self.network, strict=False).version

    @property
    def network_address(self) -> str:
        return str(ipaddress.ip_network(self.network, strict=False).network_address)

    @property
    def netmask(self) -> str:
        network = ipaddress.ip_network(self.network, strict=False)
        if network.version != 4:
            raise ValueError("netmask is only available for IPv4 static routes")
        return str(network.netmask)

    @property
    def prefixlen(self) -> int:
        return ipaddress.ip_network(self.network, strict=False).prefixlen

    @property
    def identity(self) -> tuple[str, str, str, str | None, str]:
        return (
            self.network,
            self.route_target_type,
            self.route_target_value,
            self.route_interface,
            self.comment,
        )

    @property
    def route_key(self) -> tuple[str, str, str, str | None]:
        return (
            self.network,
            self.route_target_type,
            self.route_target_value,
            self.route_interface,
        )

    @property
    def sort_key(self) -> tuple[int, int, int, str, str, str | None, str]:
        network = ipaddress.ip_network(self.network, strict=False)
        return (
            network.version,
            int(network.network_address),
            network.prefixlen,
            self.route_target_type,
            self.route_target_value,
            self.route_interface,
            self.comment,
        )


class StaticRouteState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    network: str
    route_target_type: Literal["interface", "gateway"]
    route_target_value: str
    route_interface: str | None = None
    auto: bool = False
    exclusive: bool = False
    comment: str | None = None

    @field_validator("network", mode="before")
    @classmethod
    def _validate_network(cls, value: Any) -> str:
        return str(ipaddress.ip_network(str(value), strict=False))

    @field_validator("route_target_value", "route_interface", "comment", mode="before")
    @classmethod
    def _validate_text_fields(cls, value: Any, info: Any) -> str | None:
        if value is None:
            return None
        return _require_non_blank(str(value), info.field_name)

    @model_validator(mode="after")
    def _validate_target_shape(self) -> StaticRouteState:
        if self.route_target_type == "interface" and self.route_interface is not None:
            raise ValueError("route_interface must be unset when route_target_type is interface")
        return self

    @property
    def identity(self) -> tuple[str, str, str, str | None, str | None]:
        return (
            self.network,
            self.route_target_type,
            self.route_target_value,
            self.route_interface,
            self.comment,
        )

    @property
    def route_key(self) -> tuple[str, str, str, str | None]:
        return (
            self.network,
            self.route_target_type,
            self.route_target_value,
            self.route_interface,
        )

    @property
    def sort_key(self) -> tuple[int, int, int, str, str, str | None, str]:
        network = ipaddress.ip_network(self.network, strict=False)
        return (
            network.version,
            int(network.network_address),
            network.prefixlen,
            self.route_target_type,
            self.route_target_value,
            self.route_interface,
            self.comment or "",
        )


class StaticRouteDiff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service_key: str
    to_add: tuple[StaticRouteSpec, ...] = ()
    to_remove: tuple[StaticRouteState, ...] = ()
    unchanged: tuple[StaticRouteState, ...] = ()
    has_changes: bool

    @field_validator("service_key", mode="before")
    @classmethod
    def _validate_service_key(cls, value: Any) -> str:
        return _require_non_blank(str(value), "service_key")

    @model_validator(mode="after")
    def _validate_has_changes(self) -> StaticRouteDiff:
        expected_has_changes = bool(self.to_add) or bool(self.to_remove)
        if self.has_changes != expected_has_changes:
            raise ValueError("has_changes must match to_add/to_remove state")
        return self


def build_static_route_specs(
    *,
    mapping: RouterServiceMappingConfig,
    desired_entries: Iterable[str | ObjectGroupEntry],
    service_display_name: str | None = None,
) -> tuple[StaticRouteSpec, ...]:
    typed_entries = normalize_object_group_entries(desired_entries, field_name="desired_entries")
    comment = managed_static_route_comment(
        service_key=mapping.service_key,
        display_name=service_display_name,
    )
    specs = {
        StaticRouteSpec(
            service_key=mapping.service_key,
            network=entry.value,
            route_target_type=mapping.route_target_type,
            route_target_value=mapping.route_target_value,
            route_interface=mapping.route_interface,
            auto=mapping.auto,
            exclusive=mapping.exclusive,
            comment=comment,
        )
        for entry in typed_entries
        if entry.kind in {"ipv4_network", "ipv6_network"}
    }
    return tuple(sorted(specs, key=lambda spec: spec.sort_key))


def build_static_route_diff(
    *,
    service_key: str,
    desired_routes: Iterable[StaticRouteSpec],
    actual_routes: Iterable[StaticRouteState],
) -> StaticRouteDiff:
    desired_by_identity = {route.identity: route for route in desired_routes}
    all_actual_routes = tuple(actual_routes)
    actual_by_route_key: dict[tuple[str, str, str, str | None], list[StaticRouteState]] = {}
    for route in all_actual_routes:
        actual_by_route_key.setdefault(route.route_key, []).append(route)

    for desired_route in desired_by_identity.values():
        overlapping_routes = actual_by_route_key.get(desired_route.route_key, ())
        conflicting_routes = tuple(
            route
            for route in overlapping_routes
            if not is_service_static_route_comment(comment=route.comment, service_key=service_key)
        )
        if conflicting_routes:
            conflict = conflicting_routes[0]
            marker = conflict.comment or "no managed comment"
            raise ValueError(
                "desired managed static route overlaps an unmanaged or differently "
                f"managed route: {desired_route.network} via "
                f"{desired_route.route_target_value} ({marker})"
            )

    managed_actual_routes = tuple(
        route
        for route in all_actual_routes
        if is_service_static_route_comment(comment=route.comment, service_key=service_key)
    )
    actual_by_identity = {route.identity: route for route in managed_actual_routes}

    to_add: list[StaticRouteSpec] = []
    to_remove: list[StaticRouteState] = []
    unchanged: list[StaticRouteState] = []

    for identity, desired_route in desired_by_identity.items():
        actual_route = actual_by_identity.get(identity)
        if actual_route is None:
            to_add.append(desired_route)
            continue
        if _route_options_match(desired_route=desired_route, actual_route=actual_route):
            unchanged.append(actual_route)
            continue
        to_add.append(desired_route)
        to_remove.append(actual_route)

    for identity, actual_route in actual_by_identity.items():
        if identity not in desired_by_identity:
            to_remove.append(actual_route)

    sorted_to_add = tuple(sorted(to_add, key=lambda route: route.sort_key))
    sorted_to_remove = tuple(sorted(to_remove, key=lambda route: route.sort_key))
    sorted_unchanged = tuple(sorted(unchanged, key=lambda route: route.sort_key))
    return StaticRouteDiff(
        service_key=service_key,
        to_add=sorted_to_add,
        to_remove=sorted_to_remove,
        unchanged=sorted_unchanged,
        has_changes=bool(sorted_to_add) or bool(sorted_to_remove),
    )


def _route_options_match(
    *,
    desired_route: StaticRouteSpec,
    actual_route: StaticRouteState,
) -> bool:
    if desired_route.version == 6:
        return True
    return (
        desired_route.auto == actual_route.auto
        and desired_route.exclusive == actual_route.exclusive
    )
