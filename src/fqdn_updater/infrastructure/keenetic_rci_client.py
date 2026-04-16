from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from urllib import error, request

from pydantic import BaseModel, ConfigDict, field_validator

from fqdn_updater.application.keenetic_client import KeeneticClient, KeeneticClientFactory
from fqdn_updater.domain.config_schema import RouterConfig
from fqdn_updater.domain.keenetic import (
    DnsProxyStatus,
    ObjectGroupState,
    RouteBindingSpec,
    RouteBindingState,
)

_MAX_COMMANDS_PER_BATCH = 200
_MAX_REQUEST_ATTEMPTS = 3


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


class RciConnectionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    router_id: str
    endpoint_url: str
    username: str
    password: str
    timeout_seconds: int

    @field_validator("router_id", "endpoint_url", "username", "password", mode="before")
    @classmethod
    def _validate_text_fields(cls, value: Any, info: Any) -> str:
        return _require_non_blank(str(value), info.field_name)

    @classmethod
    def from_router_config(cls, router: RouterConfig, password: str) -> RciConnectionProfile:
        return cls(
            router_id=router.id,
            endpoint_url=str(router.rci_url),
            username=router.username,
            password=password,
            timeout_seconds=router.timeout_seconds,
        )


class KeeneticRciClient(KeeneticClient):
    def __init__(self, profile: RciConnectionProfile) -> None:
        self.profile = profile
        password_manager = request.HTTPPasswordMgrWithDefaultRealm()
        password_manager.add_password(
            realm=None,
            uri=profile.endpoint_url,
            user=profile.username,
            passwd=profile.password,
        )
        digest_handler = request.HTTPDigestAuthHandler(password_manager)
        basic_handler = request.HTTPBasicAuthHandler(password_manager)
        self._opener = request.build_opener(digest_handler, basic_handler)

    def get_object_group(self, name: str) -> ObjectGroupState:
        normalized_name = _require_non_blank(name, "name")
        response_payload = self._post_commands(
            operation=f"get_object_group({normalized_name})",
            commands=[{"show": {"sc": {"object-group": {"fqdn": {}}}}}],
        )
        groups_payload = self._unwrap_response_path(
            response_payload,
            operation=f"get_object_group({normalized_name})",
            path=("show", "sc", "object-group", "fqdn"),
        )
        return self._parse_object_group_state(groups_payload=groups_payload, name=normalized_name)

    def get_route_binding(self, object_group_name: str) -> RouteBindingState:
        normalized_name = _require_non_blank(object_group_name, "object_group_name")
        response_payload = self._post_commands(
            operation=f"get_route_binding({normalized_name})",
            commands=[{"show": {"sc": {"dns-proxy": {}}}}],
        )
        dns_proxy_payload = self._unwrap_response_path(
            response_payload,
            operation=f"get_route_binding({normalized_name})",
            path=("show", "sc", "dns-proxy"),
        )
        return self._parse_route_binding_state(
            dns_proxy_payload=dns_proxy_payload,
            object_group_name=normalized_name,
        )

    def ensure_object_group(self, name: str) -> None:
        normalized_name = _require_non_blank(name, "name")
        self._post_commands(
            operation=f"ensure_object_group({normalized_name})",
            commands=[self._build_ensure_object_group_command(normalized_name)],
        )

    def remove_object_group(self, name: str) -> None:
        normalized_name = _require_non_blank(name, "name")
        self._post_commands(
            operation=f"remove_object_group({normalized_name})",
            commands=[self._build_remove_object_group_command(normalized_name)],
        )

    def add_entries(self, name: str, items: Sequence[str]) -> None:
        normalized_name = _require_non_blank(name, "name")
        normalized_items = self._normalize_items(items, field_name="items")
        commands = [
            self._build_add_entry_command(normalized_name, item) for item in normalized_items
        ]
        self._post_batched_commands(
            operation=f"add_entries({normalized_name})",
            commands=commands,
        )

    def remove_entries(self, name: str, items: Sequence[str]) -> None:
        normalized_name = _require_non_blank(name, "name")
        normalized_items = self._normalize_items(items, field_name="items")
        commands = [
            self._build_remove_entry_command(normalized_name, item) for item in normalized_items
        ]
        self._post_batched_commands(
            operation=f"remove_entries({normalized_name})",
            commands=commands,
        )

    def ensure_route(self, binding: RouteBindingSpec) -> None:
        self._post_commands(
            operation=f"ensure_route({binding.object_group_name})",
            commands=[self._build_ensure_route_command(binding)],
        )

    def remove_route(self, binding: RouteBindingState) -> None:
        try:
            self._post_commands(
                operation=f"remove_route({binding.object_group_name})",
                commands=[self._build_remove_route_command(binding)],
            )
        except RuntimeError as exc:
            if self._is_missing_route_error(exc, object_group_name=binding.object_group_name):
                return
            raise

    def save_config(self) -> None:
        self._post_commands(
            operation="save_config",
            commands=[{"parse": "system configuration save"}],
        )

    def get_dns_proxy_status(self) -> DnsProxyStatus:
        response_payload = self._post_commands(
            operation="get_dns_proxy_status",
            commands=[{"show": {"dns-proxy": {}}}],
        )
        dns_proxy_payload = self._unwrap_response_path(
            response_payload,
            operation="get_dns_proxy_status",
            path=("show", "dns-proxy"),
        )
        enabled = self._parse_dns_proxy_enabled(dns_proxy_payload)
        return DnsProxyStatus(enabled=enabled)

    def _post_commands(self, *, operation: str, commands: Sequence[dict[str, Any]]) -> Any:
        if not commands:
            return None

        request_body = json.dumps(list(commands)).encode("utf-8")
        http_request = request.Request(
            self.profile.endpoint_url,
            data=request_body,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "fqdn-updater/0.1.0",
            },
        )

        charset, response_body = self._send_request_with_retries(
            operation=operation,
            http_request=http_request,
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

    def _send_request_with_retries(
        self,
        *,
        operation: str,
        http_request: request.Request,
    ) -> tuple[str, bytes]:
        for attempt in range(1, _MAX_REQUEST_ATTEMPTS + 1):
            try:
                with self._opener.open(
                    http_request,
                    timeout=self.profile.timeout_seconds,
                ) as response:
                    charset = response.headers.get_content_charset("utf-8")
                    return charset, response.read()
            except error.HTTPError as exc:
                if exc.code in {401, 403}:
                    raise self._runtime_error(
                        operation,
                        f"authentication failed with HTTP {exc.code}",
                    ) from exc
                raise self._runtime_error(
                    operation,
                    f"request failed with HTTP {exc.code}: {exc.reason}",
                ) from exc
            except (TimeoutError, error.URLError) as exc:
                if attempt == _MAX_REQUEST_ATTEMPTS:
                    raise self._runtime_error(
                        operation,
                        "transport failed after "
                        f"{_MAX_REQUEST_ATTEMPTS} attempts: "
                        f"{self._transport_error_reason(exc)}",
                    ) from exc

        raise self._runtime_error(operation, "transport failed without response")

    def _transport_error_reason(self, exc: TimeoutError | error.URLError) -> object:
        if isinstance(exc, error.URLError):
            return getattr(exc, "reason", exc)
        return exc

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

    def _build_ensure_object_group_command(self, name: str) -> dict[str, Any]:
        return {"parse": f"object-group fqdn {self._format_cli_argument(name, 'name')}"}

    def _build_remove_object_group_command(self, name: str) -> dict[str, Any]:
        return {"parse": f"no object-group fqdn {self._format_cli_argument(name, 'name')}"}

    def _build_add_entry_command(self, name: str, item: str) -> dict[str, Any]:
        return {
            "parse": (
                "object-group fqdn "
                f"{self._format_cli_argument(name, 'name')} "
                f"include {self._format_cli_argument(item, 'item')}"
            )
        }

    def _build_remove_entry_command(self, name: str, item: str) -> dict[str, Any]:
        return {
            "parse": (
                "no object-group fqdn "
                f"{self._format_cli_argument(name, 'name')} "
                f"include {self._format_cli_argument(item, 'item')}"
            )
        }

    def _build_ensure_route_command(self, binding: RouteBindingSpec) -> dict[str, Any]:
        route_parts = [
            "dns-proxy",
            "route",
            "object-group",
            self._format_cli_argument(binding.object_group_name, "object_group_name"),
            self._format_cli_argument(binding.route_target_value, "route_target_value"),
        ]
        if binding.route_interface is not None:
            route_parts.append(
                self._format_cli_argument(binding.route_interface, "route_interface")
            )
        if binding.auto:
            route_parts.append("auto")
        if binding.exclusive:
            route_parts.append("reject")

        return {"parse": " ".join(route_parts)}

    def _build_remove_route_command(self, binding: RouteBindingState) -> dict[str, Any]:
        if not binding.exists:
            raise ValueError("binding must exist to remove route")
        if binding.route_target_value is None:
            raise ValueError("binding route_target_value must be set to remove route")

        route_parts = [
            "no",
            "dns-proxy",
            "route",
            "object-group",
            self._format_cli_argument(binding.object_group_name, "object_group_name"),
            self._format_cli_argument(binding.route_target_value, "route_target_value"),
        ]
        if binding.route_interface is not None:
            route_parts.append(
                self._format_cli_argument(binding.route_interface, "route_interface")
            )
        return {"parse": " ".join(route_parts)}

    def _is_missing_route_error(self, exc: RuntimeError, *, object_group_name: str) -> bool:
        message = str(exc)
        return "unable to find a route to" in message and f'"{object_group_name}"' in message

    def _format_cli_argument(self, value: str, field_name: str) -> str:
        normalized_value = _require_non_blank(value, field_name)
        if any(character.isspace() for character in normalized_value):
            raise ValueError(f"{field_name} must not contain whitespace")
        if '"' in normalized_value or "'" in normalized_value:
            raise ValueError(f"{field_name} must not contain quotes")
        return normalized_value

    def _raise_on_rci_status_errors(self, *, payload: Any, operation: str) -> None:
        errors = tuple(self._iter_rci_status_errors(payload))
        if not errors:
            return
        raise self._runtime_error(operation, "; ".join(errors))

    def _iter_rci_status_errors(self, payload: Any) -> tuple[str, ...]:
        errors: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                status = value.get("status")
                if isinstance(status, list):
                    for item in status:
                        if not isinstance(item, dict) or item.get("status") != "error":
                            continue
                        message = item.get("message")
                        code = item.get("code")
                        ident = item.get("ident")
                        parts = [
                            str(part)
                            for part in (ident, code, message)
                            if part is not None and str(part).strip()
                        ]
                        errors.append(" - ".join(parts) if parts else "RCI status error")
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(payload)
        return tuple(errors)

    def _unwrap_response_path(
        self,
        response_payload: Any,
        *,
        operation: str,
        path: tuple[str, ...],
    ) -> Any:
        if isinstance(response_payload, list):
            if len(response_payload) != 1:
                raise self._runtime_error(
                    operation,
                    f"expected single-command response, got list length {len(response_payload)}",
                )
            current_payload = response_payload[0]
        else:
            current_payload = response_payload

        for segment in path:
            if not isinstance(current_payload, dict):
                raise self._runtime_error(
                    operation,
                    "unexpected response shape before "
                    f"'{segment}': {type(current_payload).__name__}",
                )
            if segment not in current_payload:
                raise self._runtime_error(
                    operation,
                    f"response is missing '{segment}' at path {'/'.join(path)}",
                )
            current_payload = current_payload[segment]

        return current_payload

    def _parse_object_group_state(self, *, groups_payload: Any, name: str) -> ObjectGroupState:
        if not isinstance(groups_payload, dict):
            raise self._runtime_error(
                f"get_object_group({name})",
                f"unexpected object-group payload type {type(groups_payload).__name__}",
            )

        if self._looks_like_cli_group_container(groups_payload.get("group")):
            return self._parse_cli_style_object_group_state(
                groups_payload=groups_payload, name=name
            )

        return self._parse_config_style_object_group_state(groups_payload=groups_payload, name=name)

    def _looks_like_cli_group_container(self, payload: Any) -> bool:
        if isinstance(payload, dict):
            return "group-name" in payload
        if isinstance(payload, list):
            return all(isinstance(item, dict) and "group-name" in item for item in payload)
        return False

    def _parse_config_style_object_group_state(
        self,
        *,
        groups_payload: dict[str, Any],
        name: str,
    ) -> ObjectGroupState:
        group_payload = groups_payload.get(name)
        if group_payload is None:
            return ObjectGroupState(name=name, exists=False, entries=())
        if not isinstance(group_payload, dict):
            raise self._runtime_error(
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
            raise self._runtime_error(
                f"get_object_group({name})",
                "group "
                f"'{name}' include payload must be an object or list, got "
                f"{type(include_payload).__name__}",
            )

        entries: list[str] = []
        for item in include_items:
            if not isinstance(item, dict):
                raise self._runtime_error(
                    f"get_object_group({name})",
                    f"group '{name}' include item must be an object, got {type(item).__name__}",
                )
            address = item.get("fqdn")
            if not isinstance(address, str):
                address = item.get("address")
            if not isinstance(address, str):
                raise self._runtime_error(
                    f"get_object_group({name})",
                    "group include item is missing string field 'fqdn' or 'address'",
                )
            entries.append(address)

        return ObjectGroupState(name=name, exists=True, entries=tuple(entries))

    def _parse_cli_style_object_group_state(
        self,
        *,
        groups_payload: dict[str, Any],
        name: str,
    ) -> ObjectGroupState:
        raw_groups = groups_payload["group"]
        if isinstance(raw_groups, dict):
            group_items = [raw_groups]
        elif isinstance(raw_groups, list):
            group_items = raw_groups
        else:
            raise self._runtime_error(
                f"get_object_group({name})",
                f"group payload must be an object or list, got {type(raw_groups).__name__}",
            )

        matching_group: dict[str, Any] | None = None
        for item in group_items:
            if not isinstance(item, dict):
                raise self._runtime_error(
                    f"get_object_group({name})",
                    f"group item must be an object, got {type(item).__name__}",
                )
            group_name = item.get("group-name")
            if not isinstance(group_name, str):
                raise self._runtime_error(
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
            raise self._runtime_error(
                f"get_object_group({name})",
                f"entry payload must be an object or list, got {type(raw_entries).__name__}",
            )

        entries: list[str] = []
        for item in entry_items:
            if not isinstance(item, dict):
                raise self._runtime_error(
                    f"get_object_group({name})",
                    f"entry item must be an object, got {type(item).__name__}",
                )
            entry_type = item.get("type")
            if entry_type == "config":
                entry_value = item.get("fqdn")
                if not isinstance(entry_value, str):
                    entry_value = item.get("address")
                if not isinstance(entry_value, str):
                    raise self._runtime_error(
                        f"get_object_group({name})",
                        "config entry is missing string field 'fqdn' or 'address'",
                    )
                entries.append(entry_value)
            elif entry_type == "runtime":
                continue
            else:
                raise self._runtime_error(
                    f"get_object_group({name})",
                    f"unsupported entry type {entry_type!r}",
                )

        return ObjectGroupState(name=name, exists=True, entries=tuple(entries))

    def _parse_dns_proxy_enabled(self, dns_proxy_payload: Any) -> bool:
        if not isinstance(dns_proxy_payload, dict):
            raise self._runtime_error(
                "get_dns_proxy_status",
                f"unexpected dns-proxy payload type {type(dns_proxy_payload).__name__}",
            )

        if "enabled" in dns_proxy_payload:
            return self._coerce_boolean(
                dns_proxy_payload["enabled"],
                operation="get_dns_proxy_status",
                field_name="enabled",
            )
        if "enable" in dns_proxy_payload:
            return self._coerce_boolean(
                dns_proxy_payload["enable"],
                operation="get_dns_proxy_status",
                field_name="enable",
            )
        if "proxy-status" not in dns_proxy_payload:
            raise self._runtime_error(
                "get_dns_proxy_status",
                "response is missing 'proxy-status' and has no explicit enabled flag",
            )

        proxy_status = dns_proxy_payload["proxy-status"]
        if isinstance(proxy_status, (dict, list)):
            return bool(proxy_status)

        return self._coerce_boolean(
            proxy_status,
            operation="get_dns_proxy_status",
            field_name="proxy-status",
        )

    def _parse_route_binding_state(
        self,
        *,
        dns_proxy_payload: Any,
        object_group_name: str,
    ) -> RouteBindingState:
        if not isinstance(dns_proxy_payload, dict):
            raise self._runtime_error(
                f"get_route_binding({object_group_name})",
                f"unexpected dns-proxy payload type {type(dns_proxy_payload).__name__}",
            )

        route_entries = self._extract_route_entries(
            dns_proxy_payload=dns_proxy_payload,
            object_group_name=object_group_name,
        )
        if not route_entries:
            return RouteBindingState(object_group_name=object_group_name, exists=False)
        if len(route_entries) != 1:
            raise self._runtime_error(
                f"get_route_binding({object_group_name})",
                f"expected at most one route binding, got {len(route_entries)}",
            )

        return self._build_route_binding_state(
            raw_entry=route_entries[0],
            object_group_name=object_group_name,
        )

    def _extract_route_entries(
        self,
        *,
        dns_proxy_payload: dict[str, Any],
        object_group_name: str,
    ) -> list[dict[str, Any]]:
        route_payload = dns_proxy_payload.get("route")
        if route_payload is None:
            return []
        if isinstance(route_payload, list):
            return self._extract_route_entries_from_list(
                route_items=route_payload,
                object_group_name=object_group_name,
            )
        if not isinstance(route_payload, dict):
            raise self._runtime_error(
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
                raise self._runtime_error(
                    f"get_route_binding({object_group_name})",
                    "object-group route entry must be an object",
                )
            return [matching_entry]
        if isinstance(object_group_payload, list):
            matches: list[dict[str, Any]] = []
            for item in object_group_payload:
                if not isinstance(item, dict):
                    raise self._runtime_error(
                        f"get_route_binding({object_group_name})",
                        f"route item must be an object, got {type(item).__name__}",
                    )
                item_group_name = item.get("object-group")
                if not isinstance(item_group_name, str):
                    item_group_name = item.get("group")
                if not isinstance(item_group_name, str):
                    raise self._runtime_error(
                        f"get_route_binding({object_group_name})",
                        "route item is missing string field 'object-group' or 'group'",
                    )
                if item_group_name == object_group_name:
                    matches.append(item)
            return matches

        raise self._runtime_error(
            f"get_route_binding({object_group_name})",
            "object-group route payload must be an object or list, got "
            f"{type(object_group_payload).__name__}",
        )

    def _extract_route_entries_from_list(
        self,
        *,
        route_items: list[Any],
        object_group_name: str,
    ) -> list[dict[str, Any]]:
        matches: list[dict[str, Any]] = []
        for item in route_items:
            if not isinstance(item, dict):
                raise self._runtime_error(
                    f"get_route_binding({object_group_name})",
                    f"route item must be an object, got {type(item).__name__}",
                )
            item_group_name = item.get("object-group")
            if not isinstance(item_group_name, str):
                item_group_name = item.get("group")
            if not isinstance(item_group_name, str):
                raise self._runtime_error(
                    f"get_route_binding({object_group_name})",
                    "route item is missing string field 'object-group' or 'group'",
                )
            if item_group_name == object_group_name:
                matches.append(item)
        return matches

    def _build_route_binding_state(
        self,
        *,
        raw_entry: dict[str, Any],
        object_group_name: str,
    ) -> RouteBindingState:
        route_target_type = self._parse_route_target_type(
            raw_entry=raw_entry,
            operation=f"get_route_binding({object_group_name})",
        )
        route_target_value = self._parse_route_target_value(
            raw_entry=raw_entry,
            route_target_type=route_target_type,
            operation=f"get_route_binding({object_group_name})",
        )
        route_interface = None
        if route_target_type == "gateway":
            route_interface = self._parse_optional_string(
                raw_entry.get("interface"),
                operation=f"get_route_binding({object_group_name})",
                field_name="interface",
            )
        auto = self._parse_optional_boolean(
            raw_entry.get("auto"),
            operation=f"get_route_binding({object_group_name})",
            field_name="auto",
            default=False,
        )
        exclusive = self._parse_optional_boolean(
            raw_entry.get("reject", raw_entry.get("exclusive")),
            operation=f"get_route_binding({object_group_name})",
            field_name="reject",
            default=False,
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
        self,
        *,
        raw_entry: dict[str, Any],
        operation: str,
    ) -> str:
        explicit_type = raw_entry.get("type")
        if isinstance(explicit_type, str):
            normalized_type = explicit_type.strip().lower()
            if normalized_type in {"interface", "gateway"}:
                return normalized_type
            raise self._runtime_error(
                operation,
                f"field 'type' must be 'interface' or 'gateway', got {explicit_type!r}",
            )

        if isinstance(raw_entry.get("gateway"), str):
            return "gateway"
        if isinstance(raw_entry.get("interface"), str) and "target" not in raw_entry:
            return "interface"
        raise self._runtime_error(
            operation,
            "route entry must define either explicit type or gateway/interface target",
        )

    def _parse_route_target_value(
        self,
        *,
        raw_entry: dict[str, Any],
        route_target_type: str,
        operation: str,
    ) -> str:
        if route_target_type == "gateway":
            gateway = raw_entry.get("gateway", raw_entry.get("target"))
            if not isinstance(gateway, str):
                raise self._runtime_error(
                    operation,
                    "gateway route entry is missing string field 'gateway' or 'target'",
                )
            return _require_non_blank(gateway, "gateway")

        interface = raw_entry.get("target", raw_entry.get("interface"))
        if not isinstance(interface, str):
            raise self._runtime_error(
                operation,
                "interface route entry is missing string field 'interface' or 'target'",
            )
        return _require_non_blank(interface, "interface")

    def _parse_optional_string(
        self,
        value: Any,
        *,
        operation: str,
        field_name: str,
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise self._runtime_error(
                operation,
                f"field '{field_name}' must be a string, got {type(value).__name__}",
            )
        return _require_non_blank(value, field_name)

    def _parse_optional_boolean(
        self,
        value: Any,
        *,
        operation: str,
        field_name: str,
        default: bool,
    ) -> bool:
        if value is None:
            return default
        return self._coerce_boolean(value, operation=operation, field_name=field_name)

    def _coerce_boolean(self, value: Any, *, operation: str, field_name: str) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized_value = value.strip().lower()
            if normalized_value in {"true", "yes", "on", "enabled"}:
                return True
            if normalized_value in {"false", "no", "off", "disabled"}:
                return False

        raise self._runtime_error(
            operation,
            f"field '{field_name}' must be boolean-like, got {type(value).__name__}",
        )

    def _runtime_error(self, operation: str, message: str) -> RuntimeError:
        return RuntimeError(f"Router '{self.profile.router_id}' {operation} failed: {message}")


class KeeneticRciClientFactory(KeeneticClientFactory):
    def create(self, router: RouterConfig, password: str) -> KeeneticClient:
        return KeeneticRciClient(
            profile=RciConnectionProfile.from_router_config(router=router, password=password)
        )
