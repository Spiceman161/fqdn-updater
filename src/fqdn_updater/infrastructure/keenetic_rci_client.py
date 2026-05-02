from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from fqdn_updater.application.keenetic_client import KeeneticClient, KeeneticClientFactory
from fqdn_updater.domain.config_schema import RouterConfig
from fqdn_updater.domain.keenetic import (
    DnsProxyStatus,
    ObjectGroupState,
    RouteBindingSpec,
    RouteBindingState,
    RouteTargetCandidate,
)
from fqdn_updater.domain.static_route_diff import (
    StaticRouteSpec,
    StaticRouteState,
)
from fqdn_updater.infrastructure.keenetic_rci_commands import (
    build_add_entry_command,
    build_ensure_object_group_command,
    build_ensure_route_command,
    build_ensure_static_route_command,
    build_remove_entry_command,
    build_remove_object_group_command,
    build_remove_route_command,
    build_remove_static_route_command,
    build_save_config_command,
    show_dns_proxy_config_command,
    show_dns_proxy_status_command,
    show_interfaces_command,
    show_ip_static_routes_command,
    show_ipv6_static_routes_command,
    show_object_groups_command,
)
from fqdn_updater.infrastructure.keenetic_rci_errors import iter_rci_status_errors
from fqdn_updater.infrastructure.keenetic_rci_parsers import (
    parse_dns_proxy_status,
    parse_object_group_state,
    parse_route_binding_state,
    parse_static_routes,
    parse_wireguard_route_target_candidates,
    unwrap_response_path,
)
from fqdn_updater.infrastructure.keenetic_rci_transport import (
    KeeneticRciTransport,
    RciConnectionProfile,
)

_MAX_COMMANDS_PER_BATCH = 200


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


class KeeneticRciClient(KeeneticClient):
    def __init__(self, profile: RciConnectionProfile) -> None:
        self.profile = profile
        self._transport = KeeneticRciTransport(profile=profile)

    def get_object_group(self, name: str) -> ObjectGroupState:
        normalized_name = _require_non_blank(name, "name")
        response_payload = self._post_commands(
            operation=f"get_object_group({normalized_name})",
            commands=[show_object_groups_command()],
        )
        groups_payload = unwrap_response_path(
            response_payload,
            operation=f"get_object_group({normalized_name})",
            path=("show", "sc", "object-group", "fqdn"),
            runtime_error=self._runtime_error,
        )
        return parse_object_group_state(
            groups_payload=groups_payload,
            name=normalized_name,
            runtime_error=self._runtime_error,
        )

    def get_route_binding(self, object_group_name: str) -> RouteBindingState:
        normalized_name = _require_non_blank(object_group_name, "object_group_name")
        response_payload = self._post_commands(
            operation=f"get_route_binding({normalized_name})",
            commands=[show_dns_proxy_config_command()],
        )
        dns_proxy_payload = unwrap_response_path(
            response_payload,
            operation=f"get_route_binding({normalized_name})",
            path=("show", "sc", "dns-proxy"),
            runtime_error=self._runtime_error,
        )
        return parse_route_binding_state(
            dns_proxy_payload=dns_proxy_payload,
            object_group_name=normalized_name,
            runtime_error=self._runtime_error,
        )

    def get_static_routes(self) -> tuple[StaticRouteState, ...]:
        ip_response_payload = self._post_commands(
            operation="get_static_routes(ip)",
            commands=[show_ip_static_routes_command()],
        )
        ip_payload = unwrap_response_path(
            ip_response_payload,
            operation="get_static_routes(ip)",
            path=("show", "sc", "ip"),
            runtime_error=self._runtime_error,
        )
        if not isinstance(ip_payload, dict):
            raise self._runtime_error(
                "get_static_routes(ip)",
                f"ip payload must be an object, got {type(ip_payload).__name__}",
            )
        ip_route_payload = ip_payload.get("route", {})

        ipv6_response_payload = self._post_commands(
            operation="get_static_routes(ipv6)",
            commands=[show_ipv6_static_routes_command()],
        )
        ipv6_payload = unwrap_response_path(
            ipv6_response_payload,
            operation="get_static_routes(ipv6)",
            path=("show", "sc", "ipv6"),
            runtime_error=self._runtime_error,
        )
        if not isinstance(ipv6_payload, dict):
            raise self._runtime_error(
                "get_static_routes(ipv6)",
                f"ipv6 payload must be an object, got {type(ipv6_payload).__name__}",
            )
        ipv6_route_payload = ipv6_payload.get("route", {})

        return tuple(
            sorted(
                (
                    *parse_static_routes(
                        route_payload=ip_route_payload,
                        operation="get_static_routes(ip)",
                        runtime_error=self._runtime_error,
                    ),
                    *parse_static_routes(
                        route_payload=ipv6_route_payload,
                        operation="get_static_routes(ipv6)",
                        runtime_error=self._runtime_error,
                    ),
                ),
                key=lambda route: route.sort_key,
            )
        )

    def ensure_object_group(self, name: str) -> None:
        normalized_name = _require_non_blank(name, "name")
        self._post_commands(
            operation=f"ensure_object_group({normalized_name})",
            commands=[build_ensure_object_group_command(normalized_name)],
        )

    def remove_object_group(self, name: str) -> None:
        normalized_name = _require_non_blank(name, "name")
        self._post_commands(
            operation=f"remove_object_group({normalized_name})",
            commands=[build_remove_object_group_command(normalized_name)],
        )

    def add_entries(self, name: str, items: Sequence[str]) -> None:
        normalized_name = _require_non_blank(name, "name")
        normalized_items = self._normalize_items(items, field_name="items")
        commands = [build_add_entry_command(normalized_name, item) for item in normalized_items]
        self._post_batched_commands(
            operation=f"add_entries({normalized_name})",
            commands=commands,
        )

    def remove_entries(self, name: str, items: Sequence[str]) -> None:
        normalized_name = _require_non_blank(name, "name")
        normalized_items = self._normalize_items(items, field_name="items")
        commands = [build_remove_entry_command(normalized_name, item) for item in normalized_items]
        self._post_batched_commands(
            operation=f"remove_entries({normalized_name})",
            commands=commands,
        )

    def ensure_route(self, binding: RouteBindingSpec) -> None:
        self._post_commands(
            operation=f"ensure_route({binding.object_group_name})",
            commands=[build_ensure_route_command(binding)],
        )

    def remove_route(self, binding: RouteBindingState) -> None:
        try:
            self._post_commands(
                operation=f"remove_route({binding.object_group_name})",
                commands=[build_remove_route_command(binding)],
            )
        except RuntimeError as exc:
            if self._is_missing_route_error(exc, object_group_name=binding.object_group_name):
                return
            raise

    def ensure_static_route(self, route: StaticRouteSpec) -> None:
        self._post_commands(
            operation=f"ensure_static_route({route.network})",
            commands=[build_ensure_static_route_command(route)],
        )

    def remove_static_route(self, route: StaticRouteState) -> None:
        self._post_commands(
            operation=f"remove_static_route({route.network})",
            commands=[build_remove_static_route_command(route)],
        )

    def save_config(self) -> None:
        self._post_commands(
            operation="save_config",
            commands=[build_save_config_command()],
        )

    def get_dns_proxy_status(self) -> DnsProxyStatus:
        response_payload = self._post_commands(
            operation="get_dns_proxy_status",
            commands=[show_dns_proxy_status_command()],
        )
        dns_proxy_payload = unwrap_response_path(
            response_payload,
            operation="get_dns_proxy_status",
            path=("show", "dns-proxy"),
            runtime_error=self._runtime_error,
        )
        return parse_dns_proxy_status(
            dns_proxy_payload=dns_proxy_payload,
            runtime_error=self._runtime_error,
        )

    def discover_wireguard_route_targets(self) -> tuple[RouteTargetCandidate, ...]:
        response_payload = self._post_commands(
            operation="discover_wireguard_route_targets",
            commands=[show_interfaces_command()],
        )
        interface_payload = unwrap_response_path(
            response_payload,
            operation="discover_wireguard_route_targets",
            path=("show", "interface"),
            runtime_error=self._runtime_error,
        )
        return parse_wireguard_route_target_candidates(interface_payload)

    def _post_commands(self, *, operation: str, commands: Sequence[dict[str, Any]]) -> Any:
        if not commands:
            return None

        request_body = json.dumps(list(commands)).encode("utf-8")
        charset, response_body = self._transport.post(
            operation=operation,
            body=request_body,
            runtime_error=self._runtime_error,
        )

        try:
            decoded_body = response_body.decode(charset)
        except UnicodeDecodeError as exc:
            raise self._runtime_error(
                operation,
                f"response decode failed with charset {charset}: {exc}",
            ) from exc

        try:
            payload = json.loads(decoded_body)
        except json.JSONDecodeError as exc:
            raise self._runtime_error(operation, f"response JSON decode failed: {exc}") from exc
        self._raise_on_rci_status_errors(payload=payload, operation=operation)
        return payload

    def _post_batched_commands(
        self,
        *,
        operation: str,
        commands: Sequence[dict[str, Any]],
    ) -> None:
        for batch in self._batch_commands(commands):
            self._post_commands(operation=operation, commands=batch)

    def _batch_commands(
        self,
        commands: Sequence[dict[str, Any]],
    ) -> tuple[tuple[dict[str, Any], ...], ...]:
        if not commands:
            return ()

        batches: list[tuple[dict[str, Any], ...]] = []
        for offset in range(0, len(commands), _MAX_COMMANDS_PER_BATCH):
            batches.append(tuple(commands[offset : offset + _MAX_COMMANDS_PER_BATCH]))
        return tuple(batches)

    def _normalize_items(self, items: Sequence[str], *, field_name: str) -> tuple[str, ...]:
        normalized_items: set[str] = set()
        for item in items:
            normalized_item = str(item).strip()
            if not normalized_item:
                continue
            normalized_items.add(_require_non_blank(normalized_item, field_name))
        return tuple(sorted(normalized_items))

    def _is_missing_route_error(self, exc: RuntimeError, *, object_group_name: str) -> bool:
        message = str(exc)
        return "unable to find a route to" in message and f'"{object_group_name}"' in message

    def _raise_on_rci_status_errors(self, *, payload: Any, operation: str) -> None:
        errors = iter_rci_status_errors(payload)
        if not errors:
            return
        raise self._runtime_error(operation, "; ".join(errors))

    def _runtime_error(self, operation: str, message: str) -> RuntimeError:
        return RuntimeError(f"Router '{self.profile.router_id}' {operation} failed: {message}")


class KeeneticRciClientFactory(KeeneticClientFactory):
    def create(self, router: RouterConfig, password: str) -> KeeneticRciClient:
        return KeeneticRciClient(
            profile=RciConnectionProfile.from_router_config(router=router, password=password)
        )
