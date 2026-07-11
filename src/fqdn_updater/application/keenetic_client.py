from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from fqdn_updater.domain.config_schema import RouterConfig
from fqdn_updater.domain.keenetic import (
    DnsProxyStatus,
    ObjectGroupState,
    RouteBindingSpec,
    RouteBindingState,
    RouterInterfaceState,
)
from fqdn_updater.domain.static_route_diff import StaticRouteSpec, StaticRouteState
from fqdn_updater.domain.tls_diagnostics import TlsSanDiagnostic


class KeeneticClient(ABC):
    @abstractmethod
    def get_object_group(self, name: str) -> ObjectGroupState:
        """Read the current state of an object-group."""

    @abstractmethod
    def get_route_binding(self, object_group_name: str) -> RouteBindingState:
        """Read the current DNS route binding for an object-group."""

    @abstractmethod
    def get_static_routes(self) -> tuple[StaticRouteState, ...]:
        """Read current static routes from the router."""

    def get_interfaces(self) -> tuple[RouterInterfaceState, ...]:
        """Read current interface state from the router."""
        raise NotImplementedError

    @abstractmethod
    def ensure_object_group(self, name: str) -> None:
        """Ensure that an object-group exists."""

    @abstractmethod
    def remove_object_group(self, name: str) -> None:
        """Remove an object-group."""

    @abstractmethod
    def add_entries(self, name: str, items: Sequence[str]) -> None:
        """Add entries to an object-group."""

    @abstractmethod
    def remove_entries(self, name: str, items: Sequence[str]) -> None:
        """Remove entries from an object-group."""

    @abstractmethod
    def ensure_route(self, binding: RouteBindingSpec) -> None:
        """Ensure that a route binding matches the desired shape."""

    @abstractmethod
    def remove_route(self, binding: RouteBindingState) -> None:
        """Remove a DNS route binding for an object-group."""

    @abstractmethod
    def ensure_static_route(self, route: StaticRouteSpec) -> None:
        """Ensure that a managed static route exists."""

    @abstractmethod
    def remove_static_route(self, route: StaticRouteState) -> None:
        """Remove a managed static route."""

    def set_interface_global_priority(self, interface: str, priority: int) -> None:
        """Set an interface global priority."""
        raise NotImplementedError

    @abstractmethod
    def save_config(self) -> None:
        """Persist pending router configuration changes."""

    @abstractmethod
    def get_dns_proxy_status(self) -> DnsProxyStatus:
        """Read whether DNS proxy is enabled on the router."""

    def get_tls_san_diagnostic(self) -> TlsSanDiagnostic | None:
        """Read-only TLS/SAN diagnostic for the configured RCI endpoint.

        Non-RCI test doubles and alternate clients may opt out by returning ``None``.
        """
        return None


class KeeneticClientFactory(ABC):
    @abstractmethod
    def create(self, router: RouterConfig, password: str) -> KeeneticClient:
        """Create a client for the given router using a resolved password."""
