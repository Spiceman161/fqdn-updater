from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from fqdn_updater.application.keenetic_client import KeeneticClient, KeeneticClientFactory
from fqdn_updater.domain.config_schema import RouterConfig
from fqdn_updater.domain.keenetic import DnsProxyStatus, ObjectGroupState, RouteBindingSpec


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

    def get_object_group(self, name: str) -> ObjectGroupState:
        raise self._not_implemented("get_object_group")

    def ensure_object_group(self, name: str) -> None:
        raise self._not_implemented("ensure_object_group")

    def add_entries(self, name: str, items: Sequence[str]) -> None:
        raise self._not_implemented("add_entries")

    def remove_entries(self, name: str, items: Sequence[str]) -> None:
        raise self._not_implemented("remove_entries")

    def ensure_route(self, binding: RouteBindingSpec) -> None:
        raise self._not_implemented("ensure_route")

    def save_config(self) -> None:
        raise self._not_implemented("save_config")

    def get_dns_proxy_status(self) -> DnsProxyStatus:
        raise self._not_implemented("get_dns_proxy_status")

    def _not_implemented(self, method_name: str) -> NotImplementedError:
        return NotImplementedError(
            f"KeeneticRciClient.{method_name} is not implemented in slice S5"
        )


class KeeneticRciClientFactory(KeeneticClientFactory):
    def create(self, router: RouterConfig, password: str) -> KeeneticClient:
        return KeeneticRciClient(
            profile=RciConnectionProfile.from_router_config(router=router, password=password)
        )
