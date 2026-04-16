from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from fqdn_updater.domain.config_schema import RouterConfig
from fqdn_updater.domain.keenetic import (
    DnsProxyStatus,
    ObjectGroupState,
    RouteBindingSpec,
    RouteBindingState,
)


class KeeneticClient(ABC):
    @abstractmethod
    def get_object_group(self, name: str) -> ObjectGroupState:
        """Read the current state of an object-group."""

    @abstractmethod
    def get_route_binding(self, object_group_name: str) -> RouteBindingState:
        """Read the current DNS route binding for an object-group."""

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
    def save_config(self) -> None:
        """Persist pending router configuration changes."""

    @abstractmethod
    def get_dns_proxy_status(self) -> DnsProxyStatus:
        """Read whether DNS proxy is enabled on the router."""


class KeeneticClientFactory(ABC):
    @abstractmethod
    def create(self, router: RouterConfig, password: str) -> KeeneticClient:
        """Create a client for the given router using a resolved password."""
