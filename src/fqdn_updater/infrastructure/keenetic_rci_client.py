from __future__ import annotations

import json
import random
import socket
import ssl
import tempfile
import time
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

_MAX_COMMANDS_PER_BATCH = 200
_MAX_REQUEST_ATTEMPTS = 5
_REQUEST_RETRY_DELAYS_SECONDS = (1.0, 2.0, 4.0, 8.0)
_REQUEST_RETRY_JITTER_RATIO = 0.25
_TLS_DIAGNOSTIC_TIMEOUT_SECONDS = 5

_TransportError = TimeoutError | error.URLError | OSError | ssl.SSLError


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
        transport_errors: list[BaseException] = []
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
            except (TimeoutError, error.URLError, OSError, ssl.SSLError) as exc:
                transport_errors.append(exc)
                if attempt == _MAX_REQUEST_ATTEMPTS:
                    reason = self._transport_error_reason(exc)
                    attempt_history = self._format_transport_attempt_history(transport_errors)
                    certificate_error = self._first_certificate_verification_error(transport_errors)
                    tls_diagnostics = (
                        self._build_tls_failure_diagnostics(certificate_error)
                        if certificate_error is not None
                        else None
                    )
                    detail = (
                        f"{reason}; {attempt_history}; {tls_diagnostics}"
                        if tls_diagnostics
                        else f"{reason}; {attempt_history}"
                    )
                    raise self._runtime_error(
                        operation,
                        f"transport failed after {_MAX_REQUEST_ATTEMPTS} attempts: {detail}",
                    ) from exc
                self._sleep_before_retry(attempt)

        raise self._runtime_error(operation, "transport failed without response")

    def _sleep_before_retry(self, failed_attempt: int) -> None:
        delay = _REQUEST_RETRY_DELAYS_SECONDS[
            min(failed_attempt - 1, len(_REQUEST_RETRY_DELAYS_SECONDS) - 1)
        ]
        jitter = random.uniform(0, delay * _REQUEST_RETRY_JITTER_RATIO)
        time.sleep(delay + jitter)

    def _transport_error_reason(self, exc: _TransportError) -> object:
        if isinstance(exc, error.URLError):
            return getattr(exc, "reason", exc)
        return exc

    def _format_transport_attempt_history(self, errors: Sequence[BaseException]) -> str:
        if not errors:
            return "attempt_errors=none"
        return "attempt_errors=" + "|".join(
            f"{index}:{self._format_transport_error(exc)}"
            for index, exc in enumerate(errors, start=1)
        )

    def _format_transport_error(self, exc: BaseException) -> str:
        reason = self._transport_error_reason(exc) if self._is_transport_error(exc) else exc
        return f"{type(reason).__name__}:{reason}"

    def _first_certificate_verification_error(
        self,
        errors: Sequence[BaseException],
    ) -> _TransportError | None:
        for exc in errors:
            if self._is_transport_error(exc) and self._is_certificate_verification_error(exc):
                return exc
        return None

    def _is_transport_error(self, exc: BaseException) -> bool:
        return isinstance(exc, (TimeoutError, error.URLError, OSError, ssl.SSLError))

    def _build_tls_failure_diagnostics(self, exc: _TransportError) -> str | None:
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

    def _is_certificate_verification_error(self, exc: _TransportError) -> bool:
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
