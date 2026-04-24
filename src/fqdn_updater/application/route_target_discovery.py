from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from fqdn_updater.domain.config_schema import RouterConfig
from fqdn_updater.domain.keenetic import RouteTargetCandidate


class RouterSecretResolver(Protocol):
    def resolve(self, router: RouterConfig) -> str:
        """Resolve the password for a router."""


class RouteTargetDiscoveryClient(Protocol):
    def discover_wireguard_route_targets(self) -> tuple[RouteTargetCandidate, ...]:
        """Read WireGuard route target candidates from a router."""


class RouteTargetDiscoveryClientFactory(Protocol):
    def create(self, router: RouterConfig, password: str) -> RouteTargetDiscoveryClient:
        """Create a discovery-capable client for the given router."""


class RouteTargetDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    router_id: str
    candidates: tuple[RouteTargetCandidate, ...] = Field(default_factory=tuple)
    error_message: str | None = None

    @property
    def successful(self) -> bool:
        return self.error_message is None


class RouteTargetDiscoveryService:
    def __init__(
        self,
        *,
        secret_resolver: RouterSecretResolver,
        client_factory: RouteTargetDiscoveryClientFactory,
    ) -> None:
        self._secret_resolver = secret_resolver
        self._client_factory = client_factory

    def discover_wireguard_targets(
        self,
        *,
        router: RouterConfig,
        password_override: str | None = None,
    ) -> RouteTargetDiscoveryResult:
        try:
            password = (
                password_override
                if password_override is not None
                else self._secret_resolver.resolve(router)
            )
            client = self._client_factory.create(router=router, password=password)
            candidates = client.discover_wireguard_route_targets()
        except Exception as exc:
            return RouteTargetDiscoveryResult(
                router_id=router.id,
                error_message=str(exc),
            )

        return RouteTargetDiscoveryResult(
            router_id=router.id,
            candidates=candidates,
        )
