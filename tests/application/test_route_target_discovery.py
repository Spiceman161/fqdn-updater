from __future__ import annotations

from dataclasses import dataclass

import pytest

from fqdn_updater.application.route_target_discovery import (
    RouteTargetDiscoveryResult,
    RouteTargetDiscoveryService,
)
from fqdn_updater.domain.config_schema import RouterConfig
from fqdn_updater.domain.keenetic import RouteTargetCandidate


@dataclass
class _RecordingSecretResolver:
    password: str

    def resolve(self, router: RouterConfig) -> str:
        return self.password


@dataclass
class _RecordingClient:
    candidates: tuple[RouteTargetCandidate, ...]

    def discover_wireguard_route_targets(self) -> tuple[RouteTargetCandidate, ...]:
        return self.candidates


@dataclass
class _RecordingClientFactory:
    client: _RecordingClient
    seen_passwords: list[str]

    def create(self, router: RouterConfig, password: str) -> _RecordingClient:
        self.seen_passwords.append(password)
        return self.client


@dataclass
class _RaisingSecretResolver:
    error: Exception

    def resolve(self, router: RouterConfig) -> str:
        raise self.error


@dataclass
class _RaisingClientFactory:
    error: Exception

    def create(self, router: RouterConfig, password: str):
        raise self.error


@dataclass
class _UnusedClientFactory:
    def create(self, router: RouterConfig, password: str):
        raise AssertionError("client factory should not be called when secret resolution fails")


def test_discover_wireguard_targets_returns_typed_candidates() -> None:
    router = _router_config()
    candidates = (
        RouteTargetCandidate(
            value="Wireguard0",
            display_name="Wireguard0",
            status="connected",
            detail="type=Wireguard, Primary tunnel",
            connected=True,
        ),
        RouteTargetCandidate(
            value="Wireguard1",
            display_name="Wireguard1",
            status="down",
            detail="type=Wireguard, Backup tunnel",
            connected=False,
        ),
    )
    client_factory = _RecordingClientFactory(
        client=_RecordingClient(candidates=candidates),
        seen_passwords=[],
    )
    service = RouteTargetDiscoveryService(
        secret_resolver=_RecordingSecretResolver(password="super-secret"),
        client_factory=client_factory,
    )

    result = service.discover_wireguard_targets(router=router)

    assert result == RouteTargetDiscoveryResult(router_id="router-1", candidates=candidates)
    assert client_factory.seen_passwords == ["super-secret"]
    assert result.successful is True


def test_discover_wireguard_targets_uses_password_override_without_secret_resolution() -> None:
    router = _router_config()
    candidates = (
        RouteTargetCandidate(
            value="Wireguard7",
            display_name="Wireguard7",
            status="connected",
            detail="type=Wireguard",
            connected=True,
        ),
    )
    client_factory = _RecordingClientFactory(
        client=_RecordingClient(candidates=candidates),
        seen_passwords=[],
    )
    service = RouteTargetDiscoveryService(
        secret_resolver=_RaisingSecretResolver(RuntimeError("secret resolution failed")),
        client_factory=client_factory,
    )

    result = service.discover_wireguard_targets(
        router=router,
        password_override="generated-password",
    )

    assert result == RouteTargetDiscoveryResult(router_id="router-1", candidates=candidates)
    assert client_factory.seen_passwords == ["generated-password"]
    assert result.successful is True


@pytest.mark.parametrize(
    ("secret_resolver", "client_factory", "expected_error"),
    [
        (
            _RaisingSecretResolver(RuntimeError("secret resolution failed")),
            _UnusedClientFactory(),
            "secret resolution failed",
        ),
        (
            _RecordingSecretResolver(password="super-secret"),
            _RaisingClientFactory(ValueError("client creation failed")),
            "client creation failed",
        ),
    ],
)
def test_discover_wireguard_targets_returns_recoverable_error_result(
    secret_resolver,
    client_factory,
    expected_error: str,
) -> None:
    router = _router_config()
    service = RouteTargetDiscoveryService(
        secret_resolver=secret_resolver,
        client_factory=client_factory,
    )

    result = service.discover_wireguard_targets(router=router)

    assert result.router_id == "router-1"
    assert result.candidates == ()
    assert result.error_message == expected_error
    assert result.successful is False


def _router_config() -> RouterConfig:
    return RouterConfig.model_validate(
        {
            "id": "router-1",
            "name": "Router 1",
            "rci_url": "https://router-1.example/rci/",
            "username": "api-user",
            "password_env": "ROUTER_ONE_PASSWORD",
            "timeout_seconds": 15,
            "enabled": True,
        }
    )
