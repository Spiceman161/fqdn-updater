from __future__ import annotations

from fqdn_updater.application.keenetic_client import KeeneticClient, KeeneticClientFactory
from fqdn_updater.application.status_diagnostics import StatusDiagnosticsService
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig
from fqdn_updater.domain.keenetic import (
    DnsProxyStatus,
    ObjectGroupState,
    RouteBindingSpec,
    RouteBindingState,
)
from fqdn_updater.domain.run_artifact import RunStep
from fqdn_updater.domain.status_diagnostics import (
    OverallDiagnosticStatus,
    RouterDiagnosticStatus,
)


def test_status_diagnostics_reports_healthy_enabled_routers() -> None:
    service = StatusDiagnosticsService(
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=RecordingClientFactory(dns_proxy_enabled={"router-1": True}),
    )

    result = service.check(config=_config())

    assert result.config_ready is True
    assert result.overall_status is OverallDiagnosticStatus.HEALTHY
    assert result.checked_router_count == 1
    assert result.router_results[0].router_id == "router-1"
    assert result.router_results[0].status is RouterDiagnosticStatus.HEALTHY
    assert result.router_results[0].dns_proxy_enabled is True
    assert result.router_results[0].error_message is None
    assert result.router_results[0].failure_step is None


def test_status_diagnostics_reports_degraded_when_dns_proxy_is_disabled() -> None:
    client_factory = RecordingClientFactory(dns_proxy_enabled={"router-1": False})
    service = StatusDiagnosticsService(
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
    )

    result = service.check(config=_config())

    assert result.overall_status is OverallDiagnosticStatus.DEGRADED
    assert result.router_results[0].status is RouterDiagnosticStatus.DEGRADED
    assert result.router_results[0].dns_proxy_enabled is False
    assert client_factory.clients["router-1"].read_calls == ["dns_proxy_status"]
    assert client_factory.clients["router-1"].write_calls == []


def test_status_diagnostics_marks_secret_resolution_failures_per_router() -> None:
    service = StatusDiagnosticsService(
        secret_resolver=StubSecretResolver(errors={"router-1": "missing secret"}),
        client_factory=RecordingClientFactory(dns_proxy_enabled={"router-1": True}),
    )

    result = service.check(config=_config())

    assert result.overall_status is OverallDiagnosticStatus.FAILED
    assert result.router_results[0].status is RouterDiagnosticStatus.FAILED
    assert result.router_results[0].failure_step is RunStep.SECRET_RESOLVE
    assert result.router_results[0].error_message == "missing secret"


def test_status_diagnostics_continues_after_router_failures() -> None:
    service = StatusDiagnosticsService(
        secret_resolver=StubSecretResolver(
            passwords={"router-2": "secret-2"},
            errors={"router-1": "missing secret"},
        ),
        client_factory=RecordingClientFactory(dns_proxy_enabled={"router-2": True}),
    )

    result = service.check(config=_config_two_routers())

    assert [router.router_id for router in result.router_results] == ["router-1", "router-2"]
    assert result.router_results[0].status is RouterDiagnosticStatus.FAILED
    assert result.router_results[1].status is RouterDiagnosticStatus.HEALTHY
    assert result.overall_status is OverallDiagnosticStatus.FAILED


def test_status_diagnostics_marks_dns_proxy_read_failures() -> None:
    service = StatusDiagnosticsService(
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=RecordingClientFactory(dns_errors={"router-1": "dns-proxy read failed"}),
    )

    result = service.check(config=_config())

    assert result.overall_status is OverallDiagnosticStatus.FAILED
    assert result.router_results[0].status is RouterDiagnosticStatus.FAILED
    assert result.router_results[0].failure_step is RunStep.READ_DNS_PROXY_STATUS
    assert result.router_results[0].error_message == "dns-proxy read failed"


def test_status_diagnostics_skips_disabled_routers_and_ignores_mappings() -> None:
    client_factory = RecordingClientFactory(dns_proxy_enabled={"router-1": True, "router-2": True})
    service = StatusDiagnosticsService(
        secret_resolver=StubSecretResolver(
            passwords={"router-1": "secret-1", "router-2": "secret-2"}
        ),
        client_factory=client_factory,
    )

    result = service.check(config=_config_with_disabled_router())

    assert result.checked_router_count == 1
    assert [router.router_id for router in result.router_results] == ["router-1"]
    assert "router-2" not in client_factory.clients


class StubSecretResolver:
    def __init__(
        self,
        *,
        passwords: dict[str, str] | None = None,
        errors: dict[str, str] | None = None,
    ) -> None:
        self._passwords = passwords or {}
        self._errors = errors or {}

    def resolve(self, router: RouterConfig) -> str:
        if router.id in self._errors:
            raise RuntimeError(self._errors[router.id])
        return self._passwords[router.id]


class RecordingClient(KeeneticClient):
    def __init__(
        self,
        *,
        dns_proxy_enabled: bool | None = None,
        dns_error: str | None = None,
    ) -> None:
        self._dns_proxy_enabled = dns_proxy_enabled
        self._dns_error = dns_error
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []

    def get_object_group(self, name: str) -> ObjectGroupState:
        raise AssertionError(f"get_object_group is not used in status diagnostics: {name}")

    def get_route_binding(self, object_group_name: str) -> RouteBindingState:
        raise AssertionError(
            f"get_route_binding is not used in status diagnostics: {object_group_name}"
        )

    def ensure_object_group(self, name: str) -> None:
        self.write_calls.append(f"ensure_object_group:{name}")

    def remove_object_group(self, name: str) -> None:
        self.write_calls.append(f"remove_object_group:{name}")

    def add_entries(self, name: str, items) -> None:
        self.write_calls.append(f"add_entries:{name}")

    def remove_entries(self, name: str, items) -> None:
        self.write_calls.append(f"remove_entries:{name}")

    def ensure_route(self, binding: RouteBindingSpec) -> None:
        self.write_calls.append(f"ensure_route:{binding.object_group_name}")

    def remove_route(self, binding: RouteBindingState) -> None:
        self.write_calls.append(f"remove_route:{binding.object_group_name}")

    def save_config(self) -> None:
        self.write_calls.append("save_config")

    def get_dns_proxy_status(self) -> DnsProxyStatus:
        self.read_calls.append("dns_proxy_status")
        if self._dns_error is not None:
            raise RuntimeError(self._dns_error)
        assert self._dns_proxy_enabled is not None
        return DnsProxyStatus(enabled=self._dns_proxy_enabled)


class RecordingClientFactory(KeeneticClientFactory):
    def __init__(
        self,
        *,
        dns_proxy_enabled: dict[str, bool] | None = None,
        dns_errors: dict[str, str] | None = None,
        client_errors: dict[str, str] | None = None,
    ) -> None:
        self._dns_proxy_enabled = dns_proxy_enabled or {}
        self._dns_errors = dns_errors or {}
        self._client_errors = client_errors or {}
        self.clients: dict[str, RecordingClient] = {}

    def create(self, router: RouterConfig, password: str) -> KeeneticClient:
        if router.id in self._client_errors:
            raise RuntimeError(self._client_errors[router.id])
        client = RecordingClient(
            dns_proxy_enabled=self._dns_proxy_enabled.get(router.id),
            dns_error=self._dns_errors.get(router.id),
        )
        self.clients[router.id] = client
        return client


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_SECRET",
                    "enabled": True,
                }
            ],
            "services": [],
            "mappings": [],
        }
    )


def _config_two_routers() -> AppConfig:
    return AppConfig.model_validate(
        {
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_SECRET",
                    "enabled": True,
                },
                {
                    "id": "router-2",
                    "name": "Router 2",
                    "rci_url": "https://router-2.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_TWO_SECRET",
                    "enabled": True,
                },
            ],
            "services": [],
            "mappings": [],
        }
    )


def _config_with_disabled_router() -> AppConfig:
    return AppConfig.model_validate(
        {
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_SECRET",
                    "enabled": True,
                },
                {
                    "id": "router-2",
                    "name": "Router 2",
                    "rci_url": "https://router-2.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_TWO_SECRET",
                    "enabled": False,
                },
            ],
            "services": [],
            "mappings": [],
        }
    )
