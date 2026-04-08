from __future__ import annotations

import pytest
from pydantic import ValidationError

from fqdn_updater.domain.keenetic import DnsProxyStatus, ObjectGroupState, RouteBindingSpec


def test_object_group_state_normalizes_and_sorts_entries() -> None:
    state = ObjectGroupState(
        name="svc-telegram",
        entries=["b.example", "a.example", "b.example", "  c.example  "],
        exists=True,
    )

    assert state.entries == ("a.example", "b.example", "c.example")


def test_object_group_state_rejects_entries_for_absent_group() -> None:
    with pytest.raises(ValueError, match="entries must be empty when exists is false"):
        ObjectGroupState(name="svc-telegram", entries=["a.example"], exists=False)


def test_route_binding_spec_rejects_route_interface_for_interface_target() -> None:
    with pytest.raises(
        ValueError, match="route_interface must be unset when route_target_type is interface"
    ):
        RouteBindingSpec(
            object_group_name="svc-telegram",
            route_target_type="interface",
            route_target_value="Wireguard0",
            route_interface="Wireguard0",
        )


def test_dns_proxy_status_is_frozen() -> None:
    status = DnsProxyStatus(enabled=True)

    with pytest.raises(ValidationError):
        status.enabled = False
