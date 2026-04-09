from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any
from urllib import error, request

from pydantic import BaseModel, ConfigDict, field_validator

from fqdn_updater.application.keenetic_client import KeeneticClient, KeeneticClientFactory
from fqdn_updater.domain.config_schema import RouterConfig
from fqdn_updater.domain.keenetic import DnsProxyStatus, ObjectGroupState, RouteBindingSpec

_MAX_COMMANDS_PER_BATCH = 200


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
        self._opener = request.build_opener(digest_handler)

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

    def ensure_object_group(self, name: str) -> None:
        normalized_name = _require_non_blank(name, "name")
        self._post_commands(
            operation=f"ensure_object_group({normalized_name})",
            commands=[self._build_ensure_object_group_command(normalized_name)],
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
        raise self._not_implemented("ensure_route")

    def save_config(self) -> None:
        self._post_commands(
            operation="save_config",
            commands=[{"system": {"configuration": {"save": {}}}}],
        )

    def get_dns_proxy_status(self) -> DnsProxyStatus:
        response_payload = self._post_commands(
            operation="get_dns_proxy_status",
            commands=[{"show": {"sc": {"dns-proxy": {}}}}],
        )
        dns_proxy_payload = self._unwrap_response_path(
            response_payload,
            operation="get_dns_proxy_status",
            path=("show", "sc", "dns-proxy"),
        )
        enabled = self._parse_dns_proxy_enabled(dns_proxy_payload)
        return DnsProxyStatus(enabled=enabled)

    def _not_implemented(self, method_name: str) -> NotImplementedError:
        return NotImplementedError(
            f"KeeneticRciClient.{method_name} is not implemented in slice S12"
        )

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

        try:
            with self._opener.open(http_request, timeout=self.profile.timeout_seconds) as response:
                charset = response.headers.get_content_charset("utf-8")
                response_body = response.read()
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
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise self._runtime_error(operation, f"transport failed: {reason}") from exc

        try:
            decoded_body = response_body.decode(charset)
        except UnicodeDecodeError as exc:
            raise self._runtime_error(
                operation,
                f"response decode failed with charset {charset}: {exc}",
            ) from exc

        try:
            return json.loads(decoded_body)
        except json.JSONDecodeError as exc:
            raise self._runtime_error(operation, f"response JSON decode failed: {exc}") from exc

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
        return {
            "set": {
                "object-group": {
                    "fqdn": {
                        name: {},
                    }
                }
            }
        }

    def _build_add_entry_command(self, name: str, item: str) -> dict[str, Any]:
        return {
            "set": {
                "object-group": {
                    "fqdn": {
                        name: {
                            "include": {
                                "address": item,
                            }
                        }
                    }
                }
            }
        }

    def _build_remove_entry_command(self, name: str, item: str) -> dict[str, Any]:
        return {
            "delete": {
                "object-group": {
                    "fqdn": {
                        name: {
                            "include": {
                                "address": item,
                            }
                        }
                    }
                }
            }
        }

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

        include_payload = group_payload.get("include", ())
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
            address = item.get("address")
            if not isinstance(address, str):
                raise self._runtime_error(
                    f"get_object_group({name})",
                    "group include item is missing string field 'address'",
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
