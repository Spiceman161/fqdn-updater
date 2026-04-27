from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import tempfile
from collections.abc import Sequence
from typing import Any
from urllib import error, parse, request

from pydantic import BaseModel, ConfigDict, field_validator

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
    MANAGED_STATIC_ROUTE_COMMENT_PREFIX,
    StaticRouteSpec,
    StaticRouteState,
)

_MAX_COMMANDS_PER_BATCH = 200
_MAX_REQUEST_ATTEMPTS = 3
_TLS_DIAGNOSTIC_TIMEOUT_SECONDS = 5


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

    def get_static_routes(self) -> tuple[StaticRouteState, ...]:
        ip_response_payload = self._post_commands(
            operation="get_static_routes(ip)",
            commands=[{"show": {"sc": {"ip": {"route": {}}}}}],
        )
        ip_payload = self._unwrap_response_path(
            ip_response_payload,
            operation="get_static_routes(ip)",
            path=("show", "sc", "ip"),
        )
        if not isinstance(ip_payload, dict):
            raise self._runtime_error(
                "get_static_routes(ip)",
                f"ip payload must be an object, got {type(ip_payload).__name__}",
            )
        ip_route_payload = ip_payload.get("route", {})

        ipv6_response_payload = self._post_commands(
            operation="get_static_routes(ipv6)",
            commands=[{"show": {"sc": {"ipv6": {"route": {}}}}}],
        )
        ipv6_payload = self._unwrap_response_path(
            ipv6_response_payload,
            operation="get_static_routes(ipv6)",
            path=("show", "sc", "ipv6"),
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
                    *self._parse_static_routes(
                        route_payload=ip_route_payload,
                        operation="get_static_routes(ip)",
                    ),
                    *self._parse_static_routes(
                        route_payload=ipv6_route_payload,
                        operation="get_static_routes(ipv6)",
                    ),
                ),
                key=lambda route: route.sort_key,
            )
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

    def ensure_static_route(self, route: StaticRouteSpec) -> None:
        self._post_commands(
            operation=f"ensure_static_route({route.network})",
            commands=[self._build_ensure_static_route_command(route)],
        )

    def remove_static_route(self, route: StaticRouteState) -> None:
        self._post_commands(
            operation=f"remove_static_route({route.network})",
            commands=[self._build_remove_static_route_command(route)],
        )

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

    def discover_wireguard_route_targets(self) -> tuple[RouteTargetCandidate, ...]:
        response_payload = self._post_commands(
            operation="discover_wireguard_route_targets",
            commands=[{"show": {"interface": {}}}],
        )
        interface_payload = self._unwrap_response_path(
            response_payload,
            operation="discover_wireguard_route_targets",
            path=("show", "interface"),
        )
        return self._parse_wireguard_route_target_candidates(interface_payload)

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
                    reason = self._transport_error_reason(exc)
                    tls_diagnostics = self._build_tls_failure_diagnostics(exc)
                    detail = f"{reason}; {tls_diagnostics}" if tls_diagnostics else f"{reason}"
                    raise self._runtime_error(
                        operation,
                        f"transport failed after {_MAX_REQUEST_ATTEMPTS} attempts: {detail}",
                    ) from exc

        raise self._runtime_error(operation, "transport failed without response")

    def _transport_error_reason(self, exc: TimeoutError | error.URLError) -> object:
        if isinstance(exc, error.URLError):
            return getattr(exc, "reason", exc)
        return exc

    def _build_tls_failure_diagnostics(self, exc: TimeoutError | error.URLError) -> str | None:
        if not self._is_certificate_verification_error(exc):
            return None

        endpoint = parse.urlparse(self.profile.endpoint_url)
        host = endpoint.hostname
        if host is None:
            return (
                f"tls_diagnostics=unavailable endpoint_host=missing url={self.profile.endpoint_url}"
            )
        port = endpoint.port or (443 if endpoint.scheme == "https" else 80)
        timeout = min(self.profile.timeout_seconds, _TLS_DIAGNOSTIC_TIMEOUT_SECONDS)

        diagnostics: list[str] = [
            f"tls_diagnostics host={host} port={port} sni={host} timeout={timeout}s"
        ]
        try:
            addrinfos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as dns_exc:
            diagnostics.append(f"dns_error={dns_exc}")
            return "; ".join(diagnostics)

        endpoints = self._deduplicate_socket_addresses(addrinfos)
        if not endpoints:
            diagnostics.append("resolved_endpoints=none")
            return "; ".join(diagnostics)

        diagnostics.append(
            "resolved_endpoints="
            + ",".join(
                f"{family_name}/{ip}:{resolved_port}"
                for family_name, ip, resolved_port in endpoints
            )
        )
        for family_name, ip, resolved_port in endpoints:
            diagnostics.append(
                self._probe_tls_endpoint(
                    host=host,
                    ip=ip,
                    port=resolved_port,
                    timeout=timeout,
                    family_name=family_name,
                )
            )
        return "; ".join(diagnostics)

    def _is_certificate_verification_error(self, exc: TimeoutError | error.URLError) -> bool:
        reason = self._transport_error_reason(exc)
        if isinstance(reason, ssl.SSLCertVerificationError):
            return True
        if isinstance(reason, ssl.SSLError):
            message = str(reason)
            return "CERTIFICATE_VERIFY_FAILED" in message or "certificate verify" in message
        message = str(reason)
        return "CERTIFICATE_VERIFY_FAILED" in message or "certificate verify" in message

    def _deduplicate_socket_addresses(
        self,
        addrinfos: Sequence[tuple[int, int, int, str, tuple[str, int] | tuple[str, int, int, int]]],
    ) -> tuple[tuple[str, str, int], ...]:
        endpoints: list[tuple[str, str, int]] = []
        seen: set[tuple[str, int]] = set()
        for family, _socktype, _proto, _canonname, sockaddr in addrinfos:
            ip = sockaddr[0]
            port = sockaddr[1]
            key = (ip, port)
            if key in seen:
                continue
            seen.add(key)
            family_name = "ipv6" if family == socket.AF_INET6 else "ipv4"
            endpoints.append((family_name, ip, port))
        return tuple(endpoints)

    def _probe_tls_endpoint(
        self,
        *,
        host: str,
        ip: str,
        port: int,
        timeout: int,
        family_name: str,
    ) -> str:
        prefix = f"tls_probe {family_name}/{ip}:{port}"
        try:
            verified_context = ssl.create_default_context()
            with socket.create_connection((ip, port), timeout=timeout) as sock:
                with verified_context.wrap_socket(sock, server_hostname=host) as tls_sock:
                    cert = tls_sock.getpeercert()
                    return f"{prefix} verify=ok cert={self._format_peer_certificate(cert)}"
        except Exception as verify_exc:  # noqa: BLE001 - best-effort diagnostics.
            cert_summary = self._fetch_unverified_certificate_summary(
                host=host,
                ip=ip,
                port=port,
                timeout=timeout,
            )
            return f"{prefix} verify=failed error={verify_exc} cert={cert_summary}"

    def _fetch_unverified_certificate_summary(
        self,
        *,
        host: str,
        ip: str,
        port: int,
        timeout: int,
    ) -> str:
        try:
            unverified_context = ssl._create_unverified_context()
            with socket.create_connection((ip, port), timeout=timeout) as sock:
                with unverified_context.wrap_socket(sock, server_hostname=host) as tls_sock:
                    der = tls_sock.getpeercert(binary_form=True)
            if der is None:
                return "unavailable error=peer did not provide a certificate"
            pem = ssl.DER_cert_to_PEM_cert(der)
            with tempfile.NamedTemporaryFile("w", encoding="ascii", suffix=".pem") as cert_file:
                cert_file.write(pem)
                cert_file.flush()
                decoded = ssl._ssl._test_decode_cert(cert_file.name)  # type: ignore[attr-defined]
            return self._format_peer_certificate(decoded)
        except Exception as cert_exc:  # noqa: BLE001 - best-effort diagnostics.
            return f"unavailable error={cert_exc}"

    def _format_peer_certificate(self, cert: dict[str, Any]) -> str:
        subject = self._format_certificate_name(cert.get("subject"))
        issuer = self._format_certificate_name(cert.get("issuer"))
        subject_alt_names = [
            str(value)
            for kind, value in cert.get("subjectAltName", ())
            if str(kind).lower() == "dns"
        ]
        san = ",".join(subject_alt_names) if subject_alt_names else "-"
        not_after = cert.get("notAfter", "-")
        return f"subject={subject} issuer={issuer} san={san} notAfter={not_after}"

    def _format_certificate_name(self, value: object) -> str:
        if not isinstance(value, tuple):
            return "-"
        parts: list[str] = []
        for rdn in value:
            if not isinstance(rdn, tuple):
                continue
            for attribute in rdn:
                if (
                    isinstance(attribute, tuple)
                    and len(attribute) == 2
                    and attribute[0] == "commonName"
                ):
                    parts.append(str(attribute[1]))
        return ",".join(parts) if parts else "-"

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

    def _build_ensure_static_route_command(self, route: StaticRouteSpec) -> dict[str, Any]:
        namespace = "ip" if route.version == 4 else "ipv6"
        return {namespace: {"route": self._build_static_route_payload(route=route, remove=False)}}

    def _build_remove_static_route_command(self, route: StaticRouteState) -> dict[str, Any]:
        network = ipaddress.ip_network(route.network, strict=False)
        namespace = "ip" if network.version == 4 else "ipv6"
        return {namespace: {"route": self._build_static_route_payload(route=route, remove=True)}}

    def _build_static_route_payload(
        self,
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

    def _parse_wireguard_route_target_candidates(
        self,
        interface_payload: Any,
    ) -> tuple[RouteTargetCandidate, ...]:
        candidates_by_value: dict[str, RouteTargetCandidate] = {}
        for raw_interface in self._iter_interface_payloads(interface_payload):
            candidate = self._parse_wireguard_route_target_candidate(raw_interface)
            if candidate is None:
                continue
            candidates_by_value.setdefault(candidate.value, candidate)

        return tuple(
            sorted(
                candidates_by_value.values(),
                key=lambda candidate: candidate.value.lower(),
            )
        )

    def _iter_interface_payloads(self, payload: Any) -> tuple[dict[str, Any], ...]:
        if isinstance(payload, list):
            interfaces: list[dict[str, Any]] = []
            for item in payload:
                interfaces.extend(self._iter_interface_payloads(item))
            return tuple(interfaces)

        if not isinstance(payload, dict):
            return ()

        nested_payload = payload.get("interface")
        if nested_payload is not None:
            return self._iter_interface_payloads(nested_payload)

        if self._looks_like_interface_payload(payload):
            return (payload,)

        interfaces: list[dict[str, Any]] = []
        for interface_name, interface_payload in payload.items():
            if not isinstance(interface_payload, dict):
                continue
            normalized_payload = dict(interface_payload)
            normalized_payload.setdefault("id", interface_name)
            interfaces.append(normalized_payload)
        return tuple(interfaces)

    def _looks_like_interface_payload(self, payload: dict[str, Any]) -> bool:
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
        self,
        raw_interface: dict[str, Any],
    ) -> RouteTargetCandidate | None:
        interface_name = self._first_non_blank_string(raw_interface, ("interface-name", "name"))
        interface_id = self._first_non_blank_string(raw_interface, ("id",))
        value = interface_name or interface_id
        if value is None:
            return None

        interface_type = self._first_non_blank_string(raw_interface, ("type",))
        interface_class = self._first_non_blank_string(raw_interface, ("class",))
        description = self._first_non_blank_string(raw_interface, ("description",))
        if not self._is_wireguard_interface(
            interface_id=interface_id,
            interface_name=interface_name,
            interface_type=interface_type,
            interface_class=interface_class,
            description=description,
        ):
            return None

        connected = self._parse_optional_bool(raw_interface.get("connected"))
        state = self._first_non_blank_string(raw_interface, ("state", "link"))
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
        self,
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
        self,
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

    def _parse_optional_bool(self, value: Any) -> bool | None:
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

    def _parse_static_routes(
        self,
        *,
        route_payload: Any,
        operation: str,
    ) -> tuple[StaticRouteState, ...]:
        raw_routes = self._extract_static_route_items(
            route_payload,
            operation=operation,
        )
        parsed_routes: list[StaticRouteState] = []
        for raw_route in raw_routes:
            try:
                parsed_route = self._parse_static_route_item(
                    raw_route=raw_route,
                    operation=operation,
                )
            except ValueError as exc:
                if self._contains_managed_route_marker(raw_route):
                    raise self._runtime_error(
                        operation,
                        f"managed static route is not parseable: {exc}",
                    ) from exc
                continue
            parsed_routes.append(parsed_route)
        return tuple(sorted(parsed_routes, key=lambda route: route.sort_key))

    def _extract_static_route_items(
        self,
        route_payload: Any,
        *,
        operation: str,
    ) -> tuple[dict[str, Any], ...]:
        if route_payload is None:
            return ()
        if isinstance(route_payload, list):
            return tuple(item for item in route_payload if isinstance(item, dict))
        if not isinstance(route_payload, dict):
            raise self._runtime_error(
                operation,
                f"route payload must be an object or list, got {type(route_payload).__name__}",
            )

        for container_field in ("route", "routes", "entry", "entries"):
            nested_payload = route_payload.get(container_field)
            if nested_payload is not None:
                return self._extract_static_route_items(
                    nested_payload,
                    operation=operation,
                )

        if self._is_static_route_item_candidate(route_payload):
            return (route_payload,)

        items: list[dict[str, Any]] = []
        for value in route_payload.values():
            if isinstance(value, dict):
                if self._is_static_route_item_candidate(value):
                    items.append(value)
                else:
                    items.extend(
                        self._extract_static_route_items(
                            value,
                            operation=operation,
                        )
                    )
            elif isinstance(value, list):
                items.extend(
                    self._extract_static_route_items(
                        value,
                        operation=operation,
                    )
                )
        return tuple(items)

    def _looks_like_static_route_item(self, payload: dict[str, Any]) -> bool:
        destination_fields = {"network", "prefix", "ip", "host", "destination", "target"}
        target_fields = {"gateway", "interface"}
        return bool(destination_fields & payload.keys()) and bool(target_fields & payload.keys())

    def _is_static_route_item_candidate(self, payload: dict[str, Any]) -> bool:
        if self._looks_like_static_route_item(payload):
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
        return bool(route_fields & payload.keys()) and self._contains_managed_route_marker(payload)

    def _parse_static_route_item(
        self,
        *,
        raw_route: dict[str, Any],
        operation: str,
    ) -> StaticRouteState:
        comment = self._parse_static_route_comment(raw_route)

        network = self._parse_static_route_network(raw_route)
        route_target_type = self._parse_static_route_target_type(raw_route)
        route_target_value = self._parse_static_route_target_value(
            raw_route=raw_route,
            route_target_type=route_target_type,
        )
        route_interface = None
        if route_target_type == "gateway":
            route_interface = self._parse_optional_string(
                raw_route.get("interface"),
                operation=operation,
                field_name="interface",
            )
        auto = self._parse_optional_boolean(
            raw_route.get("auto"),
            operation=operation,
            field_name="auto",
            default=False,
        )
        exclusive = self._parse_optional_boolean(
            raw_route.get("reject", raw_route.get("exclusive")),
            operation=operation,
            field_name="reject",
            default=False,
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

    def _parse_static_route_comment(self, raw_route: dict[str, Any]) -> str | None:
        comment = raw_route.get("comment", raw_route.get("description"))
        if comment is None:
            return None
        if not isinstance(comment, str):
            raise ValueError(f"field 'comment' must be a string, got {type(comment).__name__}")
        return _require_non_blank(comment, "comment")

    def _parse_static_route_network(self, raw_route: dict[str, Any]) -> str:
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

    def _parse_static_route_target_type(self, raw_route: dict[str, Any]) -> str:
        explicit_type = raw_route.get("type")
        if isinstance(explicit_type, str):
            normalized_type = explicit_type.strip().lower()
            if normalized_type in {"interface", "gateway"}:
                return normalized_type
            raise ValueError(
                f"field 'type' must be 'interface' or 'gateway', got {explicit_type!r}"
            )

        if isinstance(raw_route.get("gateway"), str):
            return "gateway"
        if isinstance(raw_route.get("interface"), str):
            return "interface"
        raise ValueError("route is missing string gateway or interface field")

    def _parse_static_route_target_value(
        self,
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

    def _contains_managed_route_marker(self, raw_route: dict[str, Any]) -> bool:
        try:
            raw_text = json.dumps(raw_route, ensure_ascii=False, sort_keys=True)
        except TypeError:
            raw_text = str(raw_route)
        return MANAGED_STATIC_ROUTE_COMMENT_PREFIX in raw_text

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
    def create(self, router: RouterConfig, password: str) -> KeeneticRciClient:
        return KeeneticRciClient(
            profile=RciConnectionProfile.from_router_config(router=router, password=password)
        )
