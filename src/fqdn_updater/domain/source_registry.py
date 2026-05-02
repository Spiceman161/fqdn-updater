from __future__ import annotations

from fqdn_updater.domain.config_schema import ServiceDefinitionConfig, ServiceSourceConfig
from fqdn_updater.domain.source_registry_data import (
    _BASE_URL,
    _BLOCK_FILTER_SUFFIXES,
    _BLOCK_FILTERS,
    _CATEGORY_KEYS,
    _GEOBLOCK_FILTER_SUFFIXES,
    _GEOBLOCK_FILTERS,
    _HODCA_FILTER_SUFFIXES,
    _HODCA_FILTERS,
    _IPV4_SUBNET_SERVICE_KEYS,
    _IPV6_SUBNET_SERVICE_KEYS,
    _SERVICE_DESCRIPTIONS,
    _SERVICE_KEYS,
)


def _service_definition(key: str) -> ServiceDefinitionConfig:
    if key in _BLOCK_FILTERS:
        return _filtered_category_definition(
            key=key,
            category_key="block",
            include_domain_suffixes=_BLOCK_FILTERS[key],
        )
    if key == "block_other":
        return _filtered_category_definition(
            key=key,
            category_key="block",
            exclude_domain_suffixes=_BLOCK_FILTER_SUFFIXES,
        )
    if key in _GEOBLOCK_FILTERS:
        return _filtered_category_definition(
            key=key,
            category_key="geoblock",
            include_domain_suffixes=_GEOBLOCK_FILTERS[key],
        )
    if key == "geoblock_other":
        return _filtered_category_definition(
            key=key,
            category_key="geoblock",
            exclude_domain_suffixes=_GEOBLOCK_FILTER_SUFFIXES,
        )
    if key in _HODCA_FILTERS:
        return _filtered_category_definition(
            key=key,
            category_key="hodca",
            include_domain_suffixes=_HODCA_FILTERS[key],
        )
    if key == "hodca_other":
        return _filtered_category_definition(
            key=key,
            category_key="hodca",
            exclude_domain_suffixes=_HODCA_FILTER_SUFFIXES,
        )
    if key in _CATEGORY_KEYS:
        return ServiceDefinitionConfig(
            key=key,
            source_urls=[f"{_BASE_URL}/Categories/{key}.lst"],
            format="raw_domain_list",
            description=_SERVICE_DESCRIPTIONS[key],
        )

    sources = [
        ServiceSourceConfig(
            url=f"{_BASE_URL}/Services/{key}.lst",
            format="raw_domain_list",
        )
    ]
    if key in _IPV4_SUBNET_SERVICE_KEYS:
        sources.append(
            ServiceSourceConfig(
                url=f"{_BASE_URL}/Subnets/IPv4/{key}.lst",
                format="raw_cidr_list",
            )
        )
    if key in _IPV6_SUBNET_SERVICE_KEYS:
        sources.append(
            ServiceSourceConfig(
                url=f"{_BASE_URL}/Subnets/IPv6/{key}.lst",
                format="raw_cidr_list",
            )
        )

    return ServiceDefinitionConfig(
        key=key,
        sources=sources,
        description=_SERVICE_DESCRIPTIONS[key],
    )


def _filtered_category_definition(
    *,
    key: str,
    category_key: str,
    include_domain_suffixes: tuple[str, ...] = (),
    exclude_domain_suffixes: tuple[str, ...] = (),
) -> ServiceDefinitionConfig:
    return ServiceDefinitionConfig(
        key=key,
        sources=[
            ServiceSourceConfig(
                url=f"{_BASE_URL}/Categories/{category_key}.lst",
                format="raw_domain_list",
                include_domain_suffixes=list(include_domain_suffixes),
                exclude_domain_suffixes=list(exclude_domain_suffixes),
            )
        ],
        description=_SERVICE_DESCRIPTIONS[key],
    )


def builtin_service_definitions() -> list[ServiceDefinitionConfig]:
    """Return built-in source definitions used for scaffold config generation."""

    return [_service_definition(key) for key in _SERVICE_KEYS]
