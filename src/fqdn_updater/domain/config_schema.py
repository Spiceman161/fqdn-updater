from __future__ import annotations

import re
from ipaddress import ip_network
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from fqdn_updater.domain.object_group_sharding import managed_shard_names
from fqdn_updater.domain.source_normalizer import SourceFormat

_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _require_non_blank(value: str, field_name: str) -> str:
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} must not be empty")
    return normalized_value


def _require_slug(value: str, field_name: str) -> str:
    normalized_value = _require_non_blank(value, field_name)
    if not _IDENTIFIER_PATTERN.fullmatch(normalized_value):
        raise ValueError(
            f"{field_name} must use lowercase letters, digits, underscores, or hyphens"
        )
    return normalized_value


def _require_unique(values: list[str], field_name: str) -> list[str]:
    seen: set[str] = set()
    normalized_values: list[str] = []
    for value in values:
        normalized_value = _require_non_blank(value, f"{field_name} item")
        if normalized_value in seen:
            raise ValueError(f"{field_name} contains duplicate value '{normalized_value}'")
        seen.add(normalized_value)
        normalized_values.append(normalized_value)
    return normalized_values


class RouterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    rci_url: HttpUrl
    username: str
    auth_method: Literal["digest"] = "digest"
    password_env: str | None = None
    password_file: str | None = None
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=10, ge=1)
    allowed_source_ips: list[str] = Field(default_factory=list)

    @field_validator("id", "name", "username", "password_env", "password_file", mode="before")
    @classmethod
    def _validate_text_fields(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_non_blank(str(value), info.field_name)

    @field_validator("tags", "allowed_source_ips")
    @classmethod
    def _validate_string_lists(cls, values: list[str], info: Any) -> list[str]:
        return _require_unique(values, info.field_name)

    @field_validator("rci_url")
    @classmethod
    def _validate_rci_url(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("rci_url must use https")
        if value.path not in ("/rci", "/rci/"):
            raise ValueError("rci_url path must be /rci or /rci/")
        return value

    @field_validator("allowed_source_ips")
    @classmethod
    def _validate_allowed_source_ips(cls, values: list[str]) -> list[str]:
        for value in values:
            try:
                ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"allowed_source_ips item '{value}' must be a valid IP or CIDR"
                ) from exc
        return values

    @model_validator(mode="after")
    def _validate_secret_source(self) -> RouterConfig:
        has_password_env = self.password_env is not None
        has_password_file = self.password_file is not None

        if has_password_env and has_password_file:
            raise ValueError(
                f"router '{self.id}' must not define both password_env and password_file"
            )
        if self.enabled and has_password_env == has_password_file:
            raise ValueError(
                f"enabled router '{self.id}' must define exactly one of password_env or "
                "password_file"
            )
        return self


class ServiceSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    format: SourceFormat


class ServiceDefinitionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    source_urls: list[HttpUrl] = Field(default_factory=list)
    format: SourceFormat | None = None
    sources: list[ServiceSourceConfig] = Field(default_factory=list)
    enabled: bool = True
    description: str | None = None

    @field_validator("key", mode="before")
    @classmethod
    def _validate_text_fields(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_slug(str(value), info.field_name)

    @field_validator("source_urls")
    @classmethod
    def _validate_source_urls(cls, values: list[HttpUrl]) -> list[HttpUrl]:
        return values

    @model_validator(mode="after")
    def _validate_source_shape(self) -> ServiceDefinitionConfig:
        has_legacy_urls = bool(self.source_urls)
        has_legacy_format = self.format is not None
        has_sources = bool(self.sources)

        if has_sources and (has_legacy_urls or has_legacy_format):
            raise ValueError("service must define either sources or source_urls/format, not both")
        if has_sources:
            return self
        if not has_legacy_urls:
            raise ValueError("source_urls must contain at least one URL")
        if not has_legacy_format:
            raise ValueError("format must be set when source_urls are used")
        return self

    @property
    def resolved_sources(self) -> tuple[ServiceSourceConfig, ...]:
        if self.sources:
            return tuple(self.sources)
        if self.format is None:
            return ()
        return tuple(
            ServiceSourceConfig(url=source_url, format=self.format)
            for source_url in self.source_urls
        )


class RouterServiceMappingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    router_id: str
    service_key: str
    object_group_name: str
    route_target_type: Literal["interface", "gateway"]
    route_target_value: str
    route_interface: str | None = None
    exclusive: bool = True
    auto: bool = True
    managed: bool = True

    @field_validator(
        "router_id",
        "service_key",
        "object_group_name",
        "route_target_value",
        "route_interface",
        mode="before",
    )
    @classmethod
    def _validate_text_fields(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        if info.field_name in {"router_id", "service_key", "object_group_name"}:
            return _require_slug(str(value), info.field_name)
        return _require_non_blank(str(value), info.field_name)

    @model_validator(mode="after")
    def _validate_route_target(self) -> RouterServiceMappingConfig:
        if self.route_target_type == "interface" and self.route_interface is not None:
            raise ValueError("route_interface must be unset when route_target_type is interface")
        return self


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifacts_dir: str = "data/artifacts"
    logs_dir: str = "data/logs"
    log_format: Literal["text", "json"] = "text"


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    routers: list[RouterConfig] = Field(default_factory=list)
    services: list[ServiceDefinitionConfig] = Field(default_factory=list)
    mappings: list[RouterServiceMappingConfig] = Field(default_factory=list)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)

    @model_validator(mode="after")
    def _validate_relationships(self) -> AppConfig:
        errors: list[str] = []
        router_ids: set[str] = set()
        service_keys: set[str] = set()
        mapping_pairs: dict[tuple[str, str], int] = {}
        managed_object_groups: dict[tuple[str, str], int] = {}

        for router in self.routers:
            if router.id in router_ids:
                errors.append(f"duplicate router id '{router.id}'")
                continue
            router_ids.add(router.id)

        for service in self.services:
            if service.key in service_keys:
                errors.append(f"duplicate service key '{service.key}'")
                continue
            service_keys.add(service.key)

        for index, mapping in enumerate(self.mappings):
            if mapping.router_id not in router_ids:
                errors.append(
                    f"mappings[{index}].router_id references unknown router '{mapping.router_id}'"
                )
            if mapping.service_key not in service_keys:
                errors.append(
                    f"mappings[{index}].service_key references unknown service "
                    f"'{mapping.service_key}'"
                )

            mapping_pair = (mapping.router_id, mapping.service_key)
            previous_mapping_index = mapping_pairs.get(mapping_pair)
            if previous_mapping_index is not None:
                errors.append(
                    f"mappings[{index}] duplicates router/service pair from "
                    f"mappings[{previous_mapping_index}] for router '{mapping.router_id}' "
                    f"and service '{mapping.service_key}'"
                )
            else:
                mapping_pairs[mapping_pair] = index

            if mapping.managed:
                for object_group_name in managed_shard_names(mapping.object_group_name):
                    managed_object_group = (mapping.router_id, object_group_name)
                    previous_group_index = managed_object_groups.get(managed_object_group)
                    if previous_group_index is not None:
                        errors.append(
                            f"mappings[{index}] reuses managed object_group_name "
                            f"'{object_group_name}' already reserved by "
                            f"mappings[{previous_group_index}] for router '{mapping.router_id}'"
                        )
                    else:
                        managed_object_groups[managed_object_group] = index

        if errors:
            raise ValueError("; ".join(errors))

        return self

    @classmethod
    def default(cls) -> AppConfig:
        from fqdn_updater.domain.source_registry import builtin_service_definitions

        return cls(services=builtin_service_definitions())
