from __future__ import annotations

import ipaddress
from typing import Any

from fqdn_updater.domain.keenetic import RouteBindingSpec, RouteBindingState
from fqdn_updater.domain.static_route_diff import StaticRouteSpec, StaticRouteState


def show_object_groups_command() -> dict[str, Any]:
    return {"show": {"sc": {"object-group": {"fqdn": {}}}}}


def show_dns_proxy_config_command() -> dict[str, Any]:
    return {"show": {"sc": {"dns-proxy": {}}}}


def show_ip_static_routes_command() -> dict[str, Any]:
    return {"show": {"sc": {"ip": {"route": {}}}}}


def show_ipv6_static_routes_command() -> dict[str, Any]:
    return {"show": {"sc": {"ipv6": {"route": {}}}}}


def show_dns_proxy_status_command() -> dict[str, Any]:
    return {"show": {"dns-proxy": {}}}


def show_interfaces_command() -> dict[str, Any]:
    return {"show": {"interface": {}}}


def build_ensure_object_group_command(name: str) -> dict[str, Any]:
    return {"parse": f"object-group fqdn {_format_cli_argument(name, 'name')}"}


def build_remove_object_group_command(name: str) -> dict[str, Any]:
    return {"parse": f"no object-group fqdn {_format_cli_argument(name, 'name')}"}


def build_add_entry_command(name: str, item: str) -> dict[str, Any]:
    return {
        "parse": (
            "object-group fqdn "
            f"{_format_cli_argument(name, 'name')} "
            f"include {_format_cli_argument(item, 'item')}"
        )
    }


def build_remove_entry_command(name: str, item: str) -> dict[str, Any]:
    return {
        "parse": (
            "no object-group fqdn "
            f"{_format_cli_argument(name, 'name')} "
            f"include {_format_cli_argument(item, 'item')}"
        )
    }


def build_ensure_route_command(binding: RouteBindingSpec) -> dict[str, Any]:
    route_parts = [
        "dns-proxy",
        "route",
        "object-group",
        _format_cli_argument(binding.object_group_name, "object_group_name"),
        _format_cli_argument(binding.route_target_value, "route_target_value"),
    ]
    if binding.route_interface is not None:
        route_parts.append(_format_cli_argument(binding.route_interface, "route_interface"))
    if binding.auto:
        route_parts.append("auto")
    if binding.exclusive:
        route_parts.append("reject")

    return {"parse": " ".join(route_parts)}


def build_remove_route_command(binding: RouteBindingState) -> dict[str, Any]:
    if not binding.exists:
        raise ValueError("binding must exist to remove route")
    if binding.route_target_value is None:
        raise ValueError("binding route_target_value must be set to remove route")

    route_parts = [
        "no",
        "dns-proxy",
        "route",
        "object-group",
        _format_cli_argument(binding.object_group_name, "object_group_name"),
        _format_cli_argument(binding.route_target_value, "route_target_value"),
    ]
    if binding.route_interface is not None:
        route_parts.append(_format_cli_argument(binding.route_interface, "route_interface"))
    return {"parse": " ".join(route_parts)}


def build_ensure_static_route_command(route: StaticRouteSpec) -> dict[str, Any]:
    namespace = "ip" if route.version == 4 else "ipv6"
    return {namespace: {"route": _build_static_route_payload(route=route, remove=False)}}


def build_remove_static_route_command(route: StaticRouteState) -> dict[str, Any]:
    network = ipaddress.ip_network(route.network, strict=False)
    namespace = "ip" if network.version == 4 else "ipv6"
    return {namespace: {"route": _build_static_route_payload(route=route, remove=True)}}


def build_save_config_command() -> dict[str, Any]:
    return {"parse": "system configuration save"}


def _build_static_route_payload(
    *,
    route: StaticRouteSpec | StaticRouteState,
    remove: bool,
) -> dict[str, Any]:
    network = ipaddress.ip_network(route.network, strict=False)
    if network.version == 4:
        payload: dict[str, Any] = {"network": str(network.network_address)}
        payload["mask"] = str(network.netmask)
    else:
        payload = {"prefix": str(network)}

    if route.route_target_type == "gateway":
        payload["gateway"] = route.route_target_value
        if route.route_interface is not None:
            payload["interface"] = route.route_interface
    else:
        payload["interface"] = route.route_target_value

    if route.comment is not None:
        payload["comment"] = route.comment
    if not remove:
        payload["auto"] = route.auto
        payload["reject"] = route.exclusive
    if remove:
        payload["no"] = True
    return payload


def _format_cli_argument(value: str, field_name: str) -> str:
    normalized_value = _require_non_blank(value, field_name)
    if any(character.isspace() for character in normalized_value):
        raise ValueError(f"{field_name} must not contain whitespace")
    if '"' in normalized_value or "'" in normalized_value:
        raise ValueError(f"{field_name} must not contain quotes")
    return normalized_value


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value
