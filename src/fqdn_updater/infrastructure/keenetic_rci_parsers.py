from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable
from typing import Any

from fqdn_updater.domain.keenetic import (
    DnsProxyStatus,
    ObjectGroupState,
    RouteBindingState,
    RouteTargetCandidate,
)
from fqdn_updater.domain.static_route_diff import (
    MANAGED_STATIC_ROUTE_COMMENT_PREFIX,
    StaticRouteState,
)

RuntimeErrorFactory = Callable[[str, str], RuntimeError]


def unwrap_response_path(
    response_payload: Any,
    *,
    operation: str,
    path: tuple[str, ...],
    runtime_error: RuntimeErrorFactory,
) -> Any:
    if isinstance(response_payload, list):
        if len(response_payload) != 1:
            raise runtime_error(
                operation,
                f"expected single-command response, got list length {len(response_payload)}",
            )
        current_payload = response_payload[0]
    else:
        current_payload = response_payload

    for segment in path:
        if not isinstance(current_payload, dict):
            raise runtime_error(
                operation,
                f"unexpected response shape before '{segment}': {type(current_payload).__name__}",
            )
        if segment not in current_payload:
            raise runtime_error(
                operation,
                f"response is missing '{segment}' at path {'/'.join(path)}",
            )
        current_payload = current_payload[segment]

    return current_payload


def parse_wireguard_route_target_candidates(
    interface_payload: Any,
) -> tuple[RouteTargetCandidate, ...]:
    candidates_by_value: dict[str, RouteTargetCandidate] = {}
    for raw_interface in _iter_interface_payloads(interface_payload):
        candidate = _parse_wireguard_route_target_candidate(raw_interface)
        if candidate is None:
            continue
        candidates_by_value.setdefault(candidate.value, candidate)

    return tuple(
        sorted(
            candidates_by_value.values(),
            key=lambda candidate: candidate.value.lower(),
        )
    )


def parse_object_group_state(
    *,
    groups_payload: Any,
    name: str,
    runtime_error: RuntimeErrorFactory,
) -> ObjectGroupState:
    if not isinstance(groups_payload, dict):
        raise runtime_error(
            f"get_object_group({name})",
            f"unexpected object-group payload type {type(groups_payload).__name__}",
        )

    if _looks_like_cli_group_container(groups_payload.get("group")):
        return _parse_cli_style_object_group_state(
            groups_payload=groups_payload,
            name=name,
            runtime_error=runtime_error,
        )

    return _parse_config_style_object_group_state(
        groups_payload=groups_payload,
        name=name,
        runtime_error=runtime_error,
    )


def parse_static_routes(
    *,
    route_payload: Any,
    operation: str,
    runtime_error: RuntimeErrorFactory,
) -> tuple[StaticRouteState, ...]:
    raw_routes = _extract_static_route_items(
        route_payload,
        operation=operation,
        runtime_error=runtime_error,
    )
    parsed_routes: list[StaticRouteState] = []
    for raw_route in raw_routes:
        try:
            parsed_route = _parse_static_route_item(
                raw_route=raw_route,
                operation=operation,
                runtime_error=runtime_error,
            )
        except ValueError as exc:
            if _contains_managed_route_marker(raw_route):
                raise runtime_error(
                    operation,
                    f"managed static route is not parseable: {exc}",
                ) from exc
            continue
        parsed_routes.append(parsed_route)
    return tuple(sorted(parsed_routes, key=lambda route: route.sort_key))


def parse_dns_proxy_status(
    *,
    dns_proxy_payload: Any,
    runtime_error: RuntimeErrorFactory,
) -> DnsProxyStatus:
    enabled = _parse_dns_proxy_enabled(dns_proxy_payload, runtime_error=runtime_error)
    return DnsProxyStatus(enabled=enabled)


def parse_route_binding_state(
    *,
    dns_proxy_payload: Any,
    object_group_name: str,
    runtime_error: RuntimeErrorFactory,
) -> RouteBindingState:
    if not isinstance(dns_proxy_payload, dict):
        raise runtime_error(
            f"get_route_binding({object_group_name})",
            f"unexpected dns-proxy payload type {type(dns_proxy_payload).__name__}",
        )

    route_entries = _extract_route_entries(
        dns_proxy_payload=dns_proxy_payload,
        object_group_name=object_group_name,
        runtime_error=runtime_error,
    )
    if not route_entries:
        return RouteBindingState(object_group_name=object_group_name, exists=False)
    if len(route_entries) != 1:
        raise runtime_error(
            f"get_route_binding({object_group_name})",
            f"expected at most one route binding, got {len(route_entries)}",
        )

    return _build_route_binding_state(
        raw_entry=route_entries[0],
        object_group_name=object_group_name,
        runtime_error=runtime_error,
    )


def _iter_interface_payloads(payload: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, list):
        interfaces: list[dict[str, Any]] = []
        for item in payload:
            interfaces.extend(_iter_interface_payloads(item))
        return tuple(interfaces)

    if not isinstance(payload, dict):
        return ()

    nested_payload = payload.get("interface")
    if nested_payload is not None:
        return _iter_interface_payloads(nested_payload)

    if _looks_like_interface_payload(payload):
        return (payload,)

    interfaces: list[dict[str, Any]] = []
    for interface_name, interface_payload in payload.items():
        if not isinstance(interface_payload, dict):
            continue
        normalized_payload = dict(interface_payload)
        normalized_payload.setdefault("id", interface_name)
        interfaces.append(normalized_payload)
    return tuple(interfaces)


def _looks_like_interface_payload(payload: dict[str, Any]) -> bool:
    interface_fields = {
        "class",
        "id",
        "name",
        "type",
        "description",
        "interface-name",
        "link",
        "connected",
        "state",
    }
    return any(field_name in payload for field_name in interface_fields)


def _parse_wireguard_route_target_candidate(
    raw_interface: dict[str, Any],
) -> RouteTargetCandidate | None:
    interface_name = _first_non_blank_string(raw_interface, ("interface-name", "name"))
    interface_id = _first_non_blank_string(raw_interface, ("id",))
    value = interface_name or interface_id
    if value is None:
        return None

    interface_type = _first_non_blank_string(raw_interface, ("type",))
    interface_class = _first_non_blank_string(raw_interface, ("class",))
    description = _first_non_blank_string(raw_interface, ("description",))
    if not _is_wireguard_interface(
        interface_id=interface_id,
        interface_name=interface_name,
        interface_type=interface_type,
        interface_class=interface_class,
        description=description,
    ):
        return None

    connected = _parse_optional_bool(raw_interface.get("connected"))
    state = _first_non_blank_string(raw_interface, ("state", "link"))
    detail_parts = tuple(
        part
        for part in (
            f"type={interface_type}" if interface_type is not None else None,
            f"class={interface_class}" if interface_class is not None else None,
            description,
        )
        if part is not None
    )
    return RouteTargetCandidate(
        value=value,
        display_name=value,
        status=state,
        detail=", ".join(detail_parts) if detail_parts else None,
        connected=connected,
    )


def _is_wireguard_interface(
    *,
    interface_id: str | None,
    interface_name: str | None,
    interface_type: str | None,
    interface_class: str | None,
    description: str | None,
) -> bool:
    search_values = (
        interface_id or "",
        interface_name or "",
        interface_type or "",
        interface_class or "",
        description or "",
    )
    return any("wireguard" in search_value.lower() for search_value in search_values)


def _first_non_blank_string(
    payload: dict[str, Any],
    keys: tuple[str, ...],
) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        normalized_value = str(value).strip()
        if normalized_value:
            return normalized_value
    return None


def _parse_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized_value = str(value).strip().lower()
    if normalized_value in {"true", "yes", "up", "connected", "1"}:
        return True
    if normalized_value in {"false", "no", "down", "disconnected", "0"}:
        return False
    return None


def _looks_like_cli_group_container(payload: Any) -> bool:
    if isinstance(payload, dict):
        return "group-name" in payload
    if isinstance(payload, list):
        return all(isinstance(item, dict) and "group-name" in item for item in payload)
    return False


def _parse_config_style_object_group_state(
    *,
    groups_payload: dict[str, Any],
    name: str,
    runtime_error: RuntimeErrorFactory,
) -> ObjectGroupState:
    group_payload = groups_payload.get(name)
    if group_payload is None:
        return ObjectGroupState(name=name, exists=False, entries=())
    if not isinstance(group_payload, dict):
        raise runtime_error(
            f"get_object_group({name})",
            f"group '{name}' payload must be an object, got {type(group_payload).__name__}",
        )

    include_payload = group_payload.get("include")
    if include_payload is None:
        include_items: list[Any] = []
    elif isinstance(include_payload, dict):
        include_items = [include_payload]
    elif isinstance(include_payload, list):
        include_items = include_payload
    else:
        raise runtime_error(
            f"get_object_group({name})",
            "group "
            f"'{name}' include payload must be an object or list, got "
            f"{type(include_payload).__name__}",
        )

    entries: list[str] = []
    for item in include_items:
        if not isinstance(item, dict):
            raise runtime_error(
                f"get_object_group({name})",
                f"group '{name}' include item must be an object, got {type(item).__name__}",
            )
        address = item.get("fqdn")
        if not isinstance(address, str):
            address = item.get("address")
        if not isinstance(address, str):
            raise runtime_error(
                f"get_object_group({name})",
                "group include item is missing string field 'fqdn' or 'address'",
            )
        entries.append(address)

    return ObjectGroupState(name=name, exists=True, entries=tuple(entries))


def _parse_cli_style_object_group_state(
    *,
    groups_payload: dict[str, Any],
    name: str,
    runtime_error: RuntimeErrorFactory,
) -> ObjectGroupState:
    raw_groups = groups_payload["group"]
    if isinstance(raw_groups, dict):
        group_items = [raw_groups]
    elif isinstance(raw_groups, list):
        group_items = raw_groups
    else:
        raise runtime_error(
            f"get_object_group({name})",
            f"group payload must be an object or list, got {type(raw_groups).__name__}",
        )

    matching_group: dict[str, Any] | None = None
    for item in group_items:
        if not isinstance(item, dict):
            raise runtime_error(
                f"get_object_group({name})",
                f"group item must be an object, got {type(item).__name__}",
            )
        group_name = item.get("group-name")
        if not isinstance(group_name, str):
            raise runtime_error(
                f"get_object_group({name})",
                "group item is missing string field 'group-name'",
            )
        if group_name == name:
            matching_group = item
            break

    if matching_group is None:
        return ObjectGroupState(name=name, exists=False, entries=())

    raw_entries = matching_group.get("entry", ())
    if isinstance(raw_entries, dict):
        entry_items = [raw_entries]
    elif isinstance(raw_entries, list):
        entry_items = raw_entries
    elif raw_entries is None:
        entry_items = []
    else:
        raise runtime_error(
            f"get_object_group({name})",
            f"entry payload must be an object or list, got {type(raw_entries).__name__}",
        )

    entries: list[str] = []
    for item in entry_items:
        if not isinstance(item, dict):
            raise runtime_error(
                f"get_object_group({name})",
                f"entry item must be an object, got {type(item).__name__}",
            )
        entry_type = item.get("type")
        if entry_type == "config":
            entry_value = item.get("fqdn")
            if not isinstance(entry_value, str):
                entry_value = item.get("address")
            if not isinstance(entry_value, str):
                raise runtime_error(
                    f"get_object_group({name})",
                    "config entry is missing string field 'fqdn' or 'address'",
                )
            entries.append(entry_value)
        elif entry_type == "runtime":
            continue
        else:
            raise runtime_error(
                f"get_object_group({name})",
                f"unsupported entry type {entry_type!r}",
            )

    return ObjectGroupState(name=name, exists=True, entries=tuple(entries))


def _extract_static_route_items(
    route_payload: Any,
    *,
    operation: str,
    runtime_error: RuntimeErrorFactory,
) -> tuple[dict[str, Any], ...]:
    if route_payload is None:
        return ()
    if isinstance(route_payload, list):
        return tuple(item for item in route_payload if isinstance(item, dict))
    if not isinstance(route_payload, dict):
        raise runtime_error(
            operation,
            f"route payload must be an object or list, got {type(route_payload).__name__}",
        )

    for container_field in ("route", "routes", "entry", "entries"):
        nested_payload = route_payload.get(container_field)
        if nested_payload is not None:
            return _extract_static_route_items(
                nested_payload,
                operation=operation,
                runtime_error=runtime_error,
            )

    if _is_static_route_item_candidate(route_payload):
        return (route_payload,)

    items: list[dict[str, Any]] = []
    for value in route_payload.values():
        if isinstance(value, dict):
            if _is_static_route_item_candidate(value):
                items.append(value)
            else:
                items.extend(
                    _extract_static_route_items(
                        value,
                        operation=operation,
                        runtime_error=runtime_error,
                    )
                )
        elif isinstance(value, list):
            items.extend(
                _extract_static_route_items(
                    value,
                    operation=operation,
                    runtime_error=runtime_error,
                )
            )
    return tuple(items)


def _looks_like_static_route_item(payload: dict[str, Any]) -> bool:
    destination_fields = {"network", "prefix", "ip", "host", "destination", "target"}
    target_fields = {"gateway", "interface"}
    return bool(destination_fields & payload.keys()) and bool(target_fields & payload.keys())


def _is_static_route_item_candidate(payload: dict[str, Any]) -> bool:
    if _looks_like_static_route_item(payload):
        return True

    route_fields = {
        "auto",
        "comment",
        "description",
        "destination",
        "exclusive",
        "gateway",
        "host",
        "interface",
        "ip",
        "mask",
        "network",
        "prefix",
        "prefix-length",
        "prefixlen",
        "reject",
        "target",
        "type",
    }
    return bool(route_fields & payload.keys()) and _contains_managed_route_marker(payload)


def _parse_static_route_item(
    *,
    raw_route: dict[str, Any],
    operation: str,
    runtime_error: RuntimeErrorFactory,
) -> StaticRouteState:
    comment = _parse_static_route_comment(raw_route)

    network = _parse_static_route_network(raw_route)
    route_target_type = _parse_static_route_target_type(raw_route)
    route_target_value = _parse_static_route_target_value(
        raw_route=raw_route,
        route_target_type=route_target_type,
    )
    route_interface = None
    if route_target_type == "gateway":
        route_interface = _parse_optional_string(
            raw_route.get("interface"),
            operation=operation,
            field_name="interface",
            runtime_error=runtime_error,
        )
    auto = _parse_optional_boolean(
        raw_route.get("auto"),
        operation=operation,
        field_name="auto",
        default=False,
        runtime_error=runtime_error,
    )
    exclusive = _parse_optional_boolean(
        raw_route.get("reject", raw_route.get("exclusive")),
        operation=operation,
        field_name="reject",
        default=False,
        runtime_error=runtime_error,
    )
    return StaticRouteState(
        network=network,
        route_target_type=route_target_type,
        route_target_value=route_target_value,
        route_interface=route_interface,
        auto=auto,
        exclusive=exclusive,
        comment=comment,
    )


def _parse_static_route_comment(raw_route: dict[str, Any]) -> str | None:
    comment = raw_route.get("comment", raw_route.get("description"))
    if comment is None:
        return None
    if not isinstance(comment, str):
        raise ValueError(f"field 'comment' must be a string, got {type(comment).__name__}")
    return _require_non_blank(comment, "comment")


def _parse_static_route_network(raw_route: dict[str, Any]) -> str:
    raw_network = (
        raw_route.get("network")
        or raw_route.get("prefix")
        or raw_route.get("ip")
        or raw_route.get("host")
        or raw_route.get("destination")
        or raw_route.get("target")
    )
    if not isinstance(raw_network, str):
        raise ValueError("route is missing string destination field")

    raw_mask = raw_route.get("mask")
    raw_prefixlen = raw_route.get("prefixlen", raw_route.get("prefix-length"))
    if raw_mask is not None:
        if not isinstance(raw_mask, str):
            raise ValueError("field 'mask' must be a string")
        return str(ipaddress.ip_network(f"{raw_network}/{raw_mask}", strict=False))
    if raw_prefixlen is not None:
        return str(ipaddress.ip_network(f"{raw_network}/{int(raw_prefixlen)}", strict=False))
    if "/" in raw_network:
        return str(ipaddress.ip_network(raw_network, strict=False))

    address = ipaddress.ip_address(raw_network)
    prefixlen = 32 if address.version == 4 else 128
    return str(ipaddress.ip_network(f"{raw_network}/{prefixlen}", strict=False))


def _parse_static_route_target_type(raw_route: dict[str, Any]) -> str:
    explicit_type = raw_route.get("type")
    if isinstance(explicit_type, str):
        normalized_type = explicit_type.strip().lower()
        if normalized_type in {"interface", "gateway"}:
            return normalized_type
        raise ValueError(f"field 'type' must be 'interface' or 'gateway', got {explicit_type!r}")

    if isinstance(raw_route.get("gateway"), str):
        return "gateway"
    if isinstance(raw_route.get("interface"), str):
        return "interface"
    raise ValueError("route is missing string gateway or interface field")


def _parse_static_route_target_value(
    *,
    raw_route: dict[str, Any],
    route_target_type: str,
) -> str:
    if route_target_type == "gateway":
        gateway = raw_route.get("gateway")
        if not isinstance(gateway, str):
            raise ValueError("gateway route is missing string field 'gateway'")
        return _require_non_blank(gateway, "gateway")

    interface = raw_route.get("interface")
    if not isinstance(interface, str):
        raise ValueError("interface route is missing string field 'interface'")
    return _require_non_blank(interface, "interface")


def _contains_managed_route_marker(raw_route: dict[str, Any]) -> bool:
    try:
        raw_text = json.dumps(raw_route, ensure_ascii=False, sort_keys=True)
    except TypeError:
        raw_text = str(raw_route)
    return MANAGED_STATIC_ROUTE_COMMENT_PREFIX in raw_text


def _parse_dns_proxy_enabled(
    dns_proxy_payload: Any,
    *,
    runtime_error: RuntimeErrorFactory,
) -> bool:
    if not isinstance(dns_proxy_payload, dict):
        raise runtime_error(
            "get_dns_proxy_status",
            f"unexpected dns-proxy payload type {type(dns_proxy_payload).__name__}",
        )

    if "enabled" in dns_proxy_payload:
        return _coerce_boolean(
            dns_proxy_payload["enabled"],
            operation="get_dns_proxy_status",
            field_name="enabled",
            runtime_error=runtime_error,
        )
    if "enable" in dns_proxy_payload:
        return _coerce_boolean(
            dns_proxy_payload["enable"],
            operation="get_dns_proxy_status",
            field_name="enable",
            runtime_error=runtime_error,
        )
    if "proxy-status" not in dns_proxy_payload:
        raise runtime_error(
            "get_dns_proxy_status",
            "response is missing 'proxy-status' and has no explicit enabled flag",
        )

    proxy_status = dns_proxy_payload["proxy-status"]
    if isinstance(proxy_status, (dict, list)):
        return bool(proxy_status)

    return _coerce_boolean(
        proxy_status,
        operation="get_dns_proxy_status",
        field_name="proxy-status",
        runtime_error=runtime_error,
    )


def _extract_route_entries(
    *,
    dns_proxy_payload: dict[str, Any],
    object_group_name: str,
    runtime_error: RuntimeErrorFactory,
) -> list[dict[str, Any]]:
    route_payload = dns_proxy_payload.get("route")
    if route_payload is None:
        return []
    if isinstance(route_payload, list):
        return _extract_route_entries_from_list(
            route_items=route_payload,
            object_group_name=object_group_name,
            runtime_error=runtime_error,
        )
    if not isinstance(route_payload, dict):
        raise runtime_error(
            f"get_route_binding({object_group_name})",
            f"route payload must be an object, got {type(route_payload).__name__}",
        )

    object_group_payload = route_payload.get("object-group")
    if object_group_payload is None:
        return []
    if isinstance(object_group_payload, dict):
        matching_entry = object_group_payload.get(object_group_name)
        if matching_entry is None:
            return []
        if not isinstance(matching_entry, dict):
            raise runtime_error(
                f"get_route_binding({object_group_name})",
                "object-group route entry must be an object",
            )
        return [matching_entry]
    if isinstance(object_group_payload, list):
        matches: list[dict[str, Any]] = []
        for item in object_group_payload:
            if not isinstance(item, dict):
                raise runtime_error(
                    f"get_route_binding({object_group_name})",
                    f"route item must be an object, got {type(item).__name__}",
                )
            item_group_name = item.get("object-group")
            if not isinstance(item_group_name, str):
                item_group_name = item.get("group")
            if not isinstance(item_group_name, str):
                raise runtime_error(
                    f"get_route_binding({object_group_name})",
                    "route item is missing string field 'object-group' or 'group'",
                )
            if item_group_name == object_group_name:
                matches.append(item)
        return matches

    raise runtime_error(
        f"get_route_binding({object_group_name})",
        "object-group route payload must be an object or list, got "
        f"{type(object_group_payload).__name__}",
    )


def _extract_route_entries_from_list(
    *,
    route_items: list[Any],
    object_group_name: str,
    runtime_error: RuntimeErrorFactory,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in route_items:
        if not isinstance(item, dict):
            raise runtime_error(
                f"get_route_binding({object_group_name})",
                f"route item must be an object, got {type(item).__name__}",
            )
        item_group_name = item.get("object-group")
        if not isinstance(item_group_name, str):
            item_group_name = item.get("group")
        if not isinstance(item_group_name, str):
            raise runtime_error(
                f"get_route_binding({object_group_name})",
                "route item is missing string field 'object-group' or 'group'",
            )
        if item_group_name == object_group_name:
            matches.append(item)
    return matches


def _build_route_binding_state(
    *,
    raw_entry: dict[str, Any],
    object_group_name: str,
    runtime_error: RuntimeErrorFactory,
) -> RouteBindingState:
    operation = f"get_route_binding({object_group_name})"
    route_target_type = _parse_route_target_type(
        raw_entry=raw_entry,
        operation=operation,
        runtime_error=runtime_error,
    )
    route_target_value = _parse_route_target_value(
        raw_entry=raw_entry,
        route_target_type=route_target_type,
        operation=operation,
        runtime_error=runtime_error,
    )
    route_interface = None
    if route_target_type == "gateway":
        route_interface = _parse_optional_string(
            raw_entry.get("interface"),
            operation=operation,
            field_name="interface",
            runtime_error=runtime_error,
        )
    auto = _parse_optional_boolean(
        raw_entry.get("auto"),
        operation=operation,
        field_name="auto",
        default=False,
        runtime_error=runtime_error,
    )
    exclusive = _parse_optional_boolean(
        raw_entry.get("reject", raw_entry.get("exclusive")),
        operation=operation,
        field_name="reject",
        default=False,
        runtime_error=runtime_error,
    )
    return RouteBindingState(
        object_group_name=object_group_name,
        exists=True,
        route_target_type=route_target_type,
        route_target_value=route_target_value,
        route_interface=route_interface,
        auto=auto,
        exclusive=exclusive,
    )


def _parse_route_target_type(
    *,
    raw_entry: dict[str, Any],
    operation: str,
    runtime_error: RuntimeErrorFactory,
) -> str:
    explicit_type = raw_entry.get("type")
    if isinstance(explicit_type, str):
        normalized_type = explicit_type.strip().lower()
        if normalized_type in {"interface", "gateway"}:
            return normalized_type
        raise runtime_error(
            operation,
            f"field 'type' must be 'interface' or 'gateway', got {explicit_type!r}",
        )

    if isinstance(raw_entry.get("gateway"), str):
        return "gateway"
    if isinstance(raw_entry.get("interface"), str) and "target" not in raw_entry:
        return "interface"
    raise runtime_error(
        operation,
        "route entry must define either explicit type or gateway/interface target",
    )


def _parse_route_target_value(
    *,
    raw_entry: dict[str, Any],
    route_target_type: str,
    operation: str,
    runtime_error: RuntimeErrorFactory,
) -> str:
    if route_target_type == "gateway":
        gateway = raw_entry.get("gateway", raw_entry.get("target"))
        if not isinstance(gateway, str):
            raise runtime_error(
                operation,
                "gateway route entry is missing string field 'gateway' or 'target'",
            )
        return _require_non_blank(gateway, "gateway")

    interface = raw_entry.get("target", raw_entry.get("interface"))
    if not isinstance(interface, str):
        raise runtime_error(
            operation,
            "interface route entry is missing string field 'interface' or 'target'",
        )
    return _require_non_blank(interface, "interface")


def _parse_optional_string(
    value: Any,
    *,
    operation: str,
    field_name: str,
    runtime_error: RuntimeErrorFactory,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise runtime_error(
            operation,
            f"field '{field_name}' must be a string, got {type(value).__name__}",
        )
    return _require_non_blank(value, field_name)


def _parse_optional_boolean(
    value: Any,
    *,
    operation: str,
    field_name: str,
    default: bool,
    runtime_error: RuntimeErrorFactory,
) -> bool:
    if value is None:
        return default
    return _coerce_boolean(
        value,
        operation=operation,
        field_name=field_name,
        runtime_error=runtime_error,
    )


def _coerce_boolean(
    value: Any,
    *,
    operation: str,
    field_name: str,
    runtime_error: RuntimeErrorFactory,
) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized_value = value.strip().lower()
        if normalized_value in {"true", "yes", "on", "enabled"}:
            return True
        if normalized_value in {"false", "no", "off", "disabled"}:
            return False

    raise runtime_error(
        operation,
        f"field '{field_name}' must be boolean-like, got {type(value).__name__}",
    )


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value
