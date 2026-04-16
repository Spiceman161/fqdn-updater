from __future__ import annotations

import pytest

from fqdn_updater.application.run_support import validate_router_desired_fqdn_total
from fqdn_updater.domain.config_schema import RouterServiceMappingConfig
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry


def _mapping() -> RouterServiceMappingConfig:
    return RouterServiceMappingConfig.model_validate(
        {
            "router_id": "router-1",
            "service_key": "telegram",
            "object_group_name": "svc-telegram",
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "managed": True,
        }
    )


def test_validate_router_desired_fqdn_total_ignores_cidr_entries() -> None:
    desired_entries = tuple(
        ObjectGroupEntry.from_domain(f"host-{index:04d}.example") for index in range(1024)
    ) + tuple(ObjectGroupEntry.from_network(f"10.0.{index}.1/24") for index in range(32))

    validate_router_desired_fqdn_total(
        router_id="router-1",
        mappings=(_mapping(),),
        desired_entries_by_service={"telegram": desired_entries},
        source_failures_by_service={},
    )


def test_validate_router_desired_fqdn_total_still_enforces_domain_limit() -> None:
    desired_entries = tuple(
        ObjectGroupEntry.from_domain(f"host-{index:04d}.example") for index in range(1025)
    ) + tuple(ObjectGroupEntry.from_network(f"10.0.{index}.1/24") for index in range(32))

    with pytest.raises(ValueError, match="exceeding Keenetic total FQDN section limit 1024"):
        validate_router_desired_fqdn_total(
            router_id="router-1",
            mappings=(_mapping(),),
            desired_entries_by_service={"telegram": desired_entries},
            source_failures_by_service={},
        )
