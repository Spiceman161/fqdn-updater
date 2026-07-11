from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from urllib import error, request

import pytest

from fqdn_updater import __version__
from fqdn_updater.domain.config_schema import RouterConfig
from fqdn_updater.domain.tls_diagnostics import TlsEndpointDiagnostic
from fqdn_updater.infrastructure.keenetic_rci_transport import (
    KeeneticRciAcmeRepairTransport,
    KeeneticRciTransport,
    RciConnectionProfile,
    _san_matches_hostname,
)


@dataclass
class _FakeHeaders:
    charset: str = "utf-8"

    def get_content_charset(self, default: str = "utf-8") -> str:
        return self.charset or default


class _FakeResponse:
    def __init__(self, body: bytes = b'{"ok":true}', *, charset: str = "utf-8") -> None:
        self._body = body
        self.headers = _FakeHeaders(charset=charset)

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeOpener:
    def __init__(self, response: _FakeResponse | None = None) -> None:
        self._response = response or _FakeResponse()
        self.requests: list[object] = []
        self.timeouts: list[int] = []

    def open(self, http_request, timeout: int) -> _FakeResponse:
        self.requests.append(http_request)
        self.timeouts.append(timeout)
        return self._response


class _FlakyTransportOpener:
    def __init__(
        self, *, failures_before_success: int, response: _FakeResponse | None = None
    ) -> None:
        self._failures_before_success = failures_before_success
        self._response = response or _FakeResponse()
        self.requests: list[object] = []
        self.timeouts: list[int] = []

    def open(self, http_request, timeout: int) -> _FakeResponse:
        self.requests.append(http_request)
        self.timeouts.append(timeout)
        if len(self.requests) <= self._failures_before_success:
            raise error.URLError("temporary TLS failure")
        return self._response


def test_transport_opener_supports_digest_and_basic_auth(monkeypatch, profile) -> None:
    captured_handler_types: list[type[object]] = []

    def fake_build_opener(*handlers):
        captured_handler_types.extend(type(handler) for handler in handlers)
        return _FakeOpener()

    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_transport.request.build_opener",
        fake_build_opener,
    )

    KeeneticRciTransport(profile=profile)

    assert captured_handler_types == [
        request.HTTPDigestAuthHandler,
        request.HTTPBasicAuthHandler,
    ]


def test_transport_post_builds_post_request_with_timeout_and_headers(profile) -> None:
    transport = KeeneticRciTransport(profile=profile)
    opener = _FakeOpener(response=_FakeResponse(body=b'{"accepted":true}', charset="cp1251"))
    transport._opener = opener  # type: ignore[attr-defined]

    charset, response_body = transport.post(
        operation="get_dns_proxy_status",
        body=b'[{"show":{"dns-proxy":{}}}]',
        runtime_error=_runtime_error,
    )

    assert charset == "cp1251"
    assert response_body == b'{"accepted":true}'
    assert len(opener.requests) == 1
    assert opener.timeouts == [15]
    http_request = opener.requests[0]
    assert http_request.get_method() == "POST"
    assert http_request.full_url == "https://router-1.example/rci/"
    assert http_request.data == b'[{"show":{"dns-proxy":{}}}]'
    headers = {name.lower(): value for name, value in http_request.header_items()}
    assert headers["accept"] == "application/json"
    assert headers["content-type"] == "application/json"
    assert headers["user-agent"] == f"fqdn-updater/{__version__}"


def test_transport_wraps_auth_http_errors_as_runtime_errors(profile) -> None:
    transport = KeeneticRciTransport(profile=profile)

    class _FailingOpener:
        def open(self, http_request, timeout: int) -> _FakeResponse:
            raise error.HTTPError(
                url=http_request.full_url,
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=None,
            )

    transport._opener = _FailingOpener()  # type: ignore[attr-defined]

    with pytest.raises(
        RuntimeError,
        match=r"Router 'router-1' get_dns_proxy_status failed: authentication failed with HTTP 403",
    ):
        transport.post(
            operation="get_dns_proxy_status",
            body=b"[]",
            runtime_error=_runtime_error,
        )


def test_transport_wraps_non_auth_http_errors_as_runtime_errors(profile) -> None:
    transport = KeeneticRciTransport(profile=profile)

    class _FailingOpener:
        def open(self, http_request, timeout: int) -> _FakeResponse:
            raise error.HTTPError(
                url=http_request.full_url,
                code=500,
                msg="Internal Server Error",
                hdrs=None,
                fp=None,
            )

    transport._opener = _FailingOpener()  # type: ignore[attr-defined]

    with pytest.raises(
        RuntimeError,
        match=(
            r"Router 'router-1' save_config failed: "
            r"request failed with HTTP 500: Internal Server Error"
        ),
    ):
        transport.post(operation="save_config", body=b"[]", runtime_error=_runtime_error)


def test_transport_retries_transient_transport_failures(monkeypatch, profile) -> None:
    transport = KeeneticRciTransport(profile=profile)
    opener = _FlakyTransportOpener(failures_before_success=2)
    transport._opener = opener  # type: ignore[attr-defined]
    sleep_delays: list[float] = []
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_transport.time.sleep",
        lambda delay: sleep_delays.append(delay),
    )
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_transport.random.uniform",
        lambda start, end: 0.0,  # noqa: ARG005
    )

    charset, response_body = transport.post(
        operation="get_dns_proxy_status",
        body=b"[]",
        runtime_error=_runtime_error,
    )

    assert charset == "utf-8"
    assert response_body == b'{"ok":true}'
    assert len(opener.requests) == 3
    assert opener.timeouts == [15, 15, 15]
    assert sleep_delays == [1.0, 2.0]


def test_transport_reports_transport_failure_after_five_attempts(monkeypatch, profile) -> None:
    transport = KeeneticRciTransport(profile=profile)
    opener = _FlakyTransportOpener(failures_before_success=5)
    transport._opener = opener  # type: ignore[attr-defined]
    sleep_delays: list[float] = []
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_transport.time.sleep",
        lambda delay: sleep_delays.append(delay),
    )
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_transport.random.uniform",
        lambda start, end: 0.0,  # noqa: ARG005
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r"Router 'router-1' get_dns_proxy_status failed: "
            r"transport failed after 5 attempts: temporary TLS failure"
        ),
    ):
        transport.post(
            operation="get_dns_proxy_status",
            body=b"[]",
            runtime_error=_runtime_error,
        )

    assert len(opener.requests) == 5
    assert opener.timeouts == [15, 15, 15, 15, 15]
    assert sleep_delays == [1.0, 2.0, 4.0, 8.0]


def test_transport_reports_tls_diagnostics_for_certificate_failures(
    monkeypatch,
    profile,
) -> None:
    transport = KeeneticRciTransport(profile=profile)

    class _CertificateFailingOpener:
        requests: list[object]
        timeouts: list[int]

        def __init__(self) -> None:
            self.requests = []
            self.timeouts = []

        def open(self, http_request, timeout: int) -> _FakeResponse:
            self.requests.append(http_request)
            self.timeouts.append(timeout)
            raise error.URLError(
                ssl.SSLCertVerificationError("certificate verify failed: Hostname mismatch")
            )

    opener = _CertificateFailingOpener()
    transport._opener = opener  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_transport.time.sleep",
        lambda delay: None,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_transport.random.uniform",
        lambda start, end: 0.0,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_transport.socket.getaddrinfo",
        lambda host, port, type: (  # noqa: A002, ARG005
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.11", port)),
        ),
    )

    def fake_probe_tls_endpoint(
        *,
        host: str,
        ip: str,
        port: int,
        timeout: int,
        family_name: str,
    ) -> TlsEndpointDiagnostic:
        return TlsEndpointDiagnostic(
            address=ip,
            family=family_name,
            port=port,
            subject="wrong.example",
            issuer="Test CA",
            subject_alt_names=("wrong.example",),
            san_matches_hostname=False,
        )

    monkeypatch.setattr(transport, "_probe_tls_endpoint", fake_probe_tls_endpoint)

    with pytest.raises(RuntimeError) as exc_info:
        transport.post(
            operation="get_dns_proxy_status",
            body=b"[]",
            runtime_error=_runtime_error,
        )

    message = str(exc_info.value)
    assert "transport failed after 5 attempts" in message
    assert "certificate verify failed: Hostname mismatch" in message
    assert "attempt_errors=1:SSLCertVerificationError:" in message
    assert "tls_san hostname=router-1.example complete=True san_matches=False" in message
    assert "ipv4/203.0.113.10:443:ok:san_match=False" in message
    assert len(opener.requests) == 5
    assert opener.timeouts == [15, 15, 15, 15, 15]


def test_tls_san_diagnostic_uses_router_timeout_up_to_thirty_seconds(
    monkeypatch,
    profile,
) -> None:
    transport = KeeneticRciTransport(profile=profile.model_copy(update={"timeout_seconds": 45}))
    observed_timeouts: list[int] = []
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_transport.socket.getaddrinfo",
        lambda host, port, type: (  # noqa: A002, ARG005
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", port)),
        ),
    )

    def fake_probe_tls_endpoint(
        *,
        host: str,
        ip: str,
        port: int,
        timeout: int,
        family_name: str,
    ) -> TlsEndpointDiagnostic:
        observed_timeouts.append(timeout)
        return TlsEndpointDiagnostic(
            address=ip,
            family=family_name,
            port=port,
            subject_alt_names=(host,),
            san_matches_hostname=True,
        )

    monkeypatch.setattr(transport, "_probe_tls_endpoint", fake_probe_tls_endpoint)

    diagnostic = transport.get_tls_san_diagnostic()

    assert diagnostic.is_healthy is True
    assert observed_timeouts == [30]


def test_tls_san_diagnostic_respects_shorter_router_timeout(monkeypatch, profile) -> None:
    transport = KeeneticRciTransport(profile=profile)
    observed_timeouts: list[int] = []
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_transport.socket.getaddrinfo",
        lambda host, port, type: (  # noqa: A002, ARG005
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", port)),
        ),
    )

    def fake_probe_tls_endpoint(
        *,
        host: str,
        ip: str,
        port: int,
        timeout: int,
        family_name: str,
    ) -> TlsEndpointDiagnostic:
        observed_timeouts.append(timeout)
        return TlsEndpointDiagnostic(
            address=ip,
            family=family_name,
            port=port,
            subject_alt_names=(host,),
            san_matches_hostname=True,
        )

    monkeypatch.setattr(transport, "_probe_tls_endpoint", fake_probe_tls_endpoint)

    transport.get_tls_san_diagnostic()

    assert observed_timeouts == [15]


def test_san_matching_supports_exact_and_single_label_wildcards_only() -> None:
    assert _san_matches_hostname("rci.example.test", "rci.example.test") is True
    assert _san_matches_hostname("*.example.test", "rci.example.test") is True
    assert _san_matches_hostname("*.example.test", "a.rci.example.test") is False
    assert _san_matches_hostname("other.example.test", "rci.example.test") is False


def test_acme_unverified_transport_rejects_non_rci_or_different_hostname(profile) -> None:
    with pytest.raises(ValueError, match="exactly match"):
        KeeneticRciAcmeRepairTransport(profile, hostname="rci.other.example")
    with pytest.raises(ValueError, match="rci"):
        KeeneticRciAcmeRepairTransport(profile, hostname="router-1.example")


def _runtime_error(operation: str, message: str) -> RuntimeError:
    return RuntimeError(f"Router 'router-1' {operation} failed: {message}")


@pytest.fixture
def profile() -> RciConnectionProfile:
    return RciConnectionProfile.from_router_config(
        router=RouterConfig.model_validate(
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_PASSWORD",
                "timeout_seconds": 15,
                "enabled": True,
            }
        ),
        password="secret",
    )
