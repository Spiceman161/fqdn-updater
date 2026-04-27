from __future__ import annotations

import json
import socket
import ssl
from dataclasses import dataclass
from urllib import error, request

import pytest

from fqdn_updater.domain.keenetic import (
    RouteBindingSpec,
    RouteBindingState,
    RouteTargetCandidate,
)
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.static_route_diff import StaticRouteSpec, StaticRouteState
from fqdn_updater.infrastructure.keenetic_rci_client import (
    KeeneticRciClient,
    KeeneticRciClientFactory,
    RciConnectionProfile,
)


@dataclass
class _FakeHeaders:
    charset: str = "utf-8"

    def get_content_charset(self, default: str = "utf-8") -> str:
        return self.charset or default


class _FakeResponse:
    def __init__(self, payload: object, *, charset: str = "utf-8") -> None:
        self._payload = payload
        self.headers = _FakeHeaders(charset=charset)

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeOpener:
    def __init__(self, payload: object | tuple[object, ...]) -> None:
        if isinstance(payload, tuple):
            self._payloads = list(payload)
        else:
            self._payloads = [payload]
        self.requests: list[object] = []
        self.timeouts: list[int] = []

    def open(self, http_request, timeout: int) -> _FakeResponse:
        self.requests.append(http_request)
        self.timeouts.append(timeout)
        payload = self._payloads.pop(0) if self._payloads else {}
        return _FakeResponse(payload)


class _FlakyTransportOpener:
    def __init__(self, *, failures_before_success: int, payload: object) -> None:
        self._failures_before_success = failures_before_success
        self._payload = payload
        self.requests: list[object] = []
        self.timeouts: list[int] = []

    def open(self, http_request, timeout: int) -> _FakeResponse:
        self.requests.append(http_request)
        self.timeouts.append(timeout)
        if len(self.requests) <= self._failures_before_success:
            raise error.URLError("temporary TLS failure")
        return _FakeResponse(self._payload)


def _make_client(
    router_config, payload: object | tuple[object, ...]
) -> tuple[KeeneticRciClient, _FakeOpener]:
    client = KeeneticRciClientFactory().create(router=router_config, password="secret")
    opener = _FakeOpener(payload)
    client._opener = opener  # type: ignore[attr-defined]
    return client, opener


def test_connection_profile_builds_from_router_config(router_config) -> None:
    profile = RciConnectionProfile.from_router_config(router=router_config, password="secret")

    assert profile.router_id == "router-1"
    assert profile.endpoint_url == "https://router-1.example/rci/"
    assert profile.username == "api-user"
    assert profile.password == "secret"
    assert profile.timeout_seconds == 15


def test_factory_creates_rci_client_with_profile(router_config) -> None:
    client = KeeneticRciClientFactory().create(router=router_config, password="secret")

    assert isinstance(client, KeeneticRciClient)
    assert client.profile.router_id == "router-1"


def test_client_opener_supports_digest_and_basic_auth(monkeypatch, router_config) -> None:
    captured_handler_types: list[type[object]] = []

    def fake_build_opener(*handlers):
        captured_handler_types.extend(type(handler) for handler in handlers)
        return _FakeOpener({})

    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_client.request.build_opener",
        fake_build_opener,
    )

    KeeneticRciClientFactory().create(router=router_config, password="secret")

    assert captured_handler_types == [
        request.HTTPDigestAuthHandler,
        request.HTTPBasicAuthHandler,
    ]


def test_get_object_group_parses_cli_style_payload_and_ignores_runtime_entries(
    router_config,
) -> None:
    payload = [
        {
            "show": {
                "sc": {
                    "object-group": {
                        "fqdn": {
                            "group": [
                                {
                                    "group-name": "svc-other",
                                    "entry": [{"type": "config", "fqdn": "ignore.example"}],
                                },
                                {
                                    "group-name": "svc-telegram",
                                    "entry": [
                                        {"type": "runtime", "fqdn": "runtime.example"},
                                        {"type": "config", "address": "2001:db8::1/64"},
                                        {"type": "config", "fqdn": "B.Example.com."},
                                        {"type": "config", "address": "10.0.0.1/24"},
                                    ],
                                },
                            ]
                        }
                    }
                }
            }
        }
    ]
    client, opener = _make_client(router_config, payload)

    state = client.get_object_group("svc-telegram")

    assert state.name == "svc-telegram"
    assert state.exists is True
    assert state.entries == ("10.0.0.1/24", "2001:db8::1/64", "B.Example.com.")
    assert state.typed_entries == (
        ObjectGroupEntry.from_domain("b.example.com"),
        ObjectGroupEntry.from_network("10.0.0.0/24"),
        ObjectGroupEntry.from_network("2001:db8::/64"),
    )
    assert len(opener.requests) == 1
    assert opener.timeouts == [15]
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {"show": {"sc": {"object-group": {"fqdn": {}}}}}
    ]


def test_get_object_group_parses_config_style_payload(router_config) -> None:
    payload = [
        {
            "show": {
                "sc": {
                    "object-group": {
                        "fqdn": {
                            "svc-telegram": {
                                "include": [
                                    {"address": "2001:db8::1/64"},
                                    {"fqdn": "B.Example.com."},
                                    {"address": "10.0.0.1/24"},
                                ]
                            }
                        }
                    }
                }
            }
        }
    ]
    client, opener = _make_client(router_config, payload)

    state = client.get_object_group("svc-telegram")

    assert state.name == "svc-telegram"
    assert state.exists is True
    assert state.entries == ("10.0.0.1/24", "2001:db8::1/64", "B.Example.com.")
    assert state.typed_entries == (
        ObjectGroupEntry.from_domain("b.example.com"),
        ObjectGroupEntry.from_network("10.0.0.0/24"),
        ObjectGroupEntry.from_network("2001:db8::/64"),
    )
    assert len(opener.requests) == 1


def test_get_object_group_parses_config_style_payload_with_single_include_object(
    router_config,
) -> None:
    payload = [
        {
            "show": {
                "sc": {
                    "object-group": {
                        "fqdn": {
                            "svc-telegram": {
                                "include": {"address": "a.example"},
                            }
                        }
                    }
                }
            }
        }
    ]
    client, _ = _make_client(router_config, payload)

    state = client.get_object_group("svc-telegram")

    assert state.name == "svc-telegram"
    assert state.exists is True
    assert state.entries == ("a.example",)


def test_get_object_group_parses_config_style_payload_with_missing_include_as_empty(
    router_config,
) -> None:
    payload = [
        {
            "show": {
                "sc": {
                    "object-group": {
                        "fqdn": {
                            "svc-telegram": {},
                        }
                    }
                }
            }
        }
    ]
    client, _ = _make_client(router_config, payload)

    state = client.get_object_group("svc-telegram")

    assert state.name == "svc-telegram"
    assert state.exists is True
    assert state.entries == ()


def test_get_object_group_parses_config_style_payload_for_group_named_group(
    router_config,
) -> None:
    payload = [
        {
            "show": {
                "sc": {
                    "object-group": {
                        "fqdn": {
                            "group": {
                                "include": [
                                    {"address": "b.example"},
                                    {"address": "a.example"},
                                ]
                            }
                        }
                    }
                }
            }
        }
    ]
    client, _ = _make_client(router_config, payload)

    state = client.get_object_group("group")

    assert state.name == "group"
    assert state.exists is True
    assert state.entries == ("a.example", "b.example")


@pytest.mark.parametrize(
    ("payload", "name"),
    [
        (
            [
                {
                    "show": {
                        "sc": {
                            "object-group": {
                                "fqdn": {
                                    "group": [
                                        {
                                            "group-name": "svc-other",
                                            "entry": [{"type": "config", "fqdn": "a.example"}],
                                        }
                                    ]
                                }
                            }
                        }
                    }
                }
            ],
            "svc-telegram",
        ),
        (
            [
                {
                    "show": {
                        "sc": {
                            "object-group": {
                                "fqdn": {"svc-other": {"include": [{"address": "a.example"}]}}
                            }
                        }
                    }
                }
            ],
            "svc-telegram",
        ),
    ],
)
def test_get_object_group_reports_absence_only_when_group_is_explicitly_missing(
    router_config, payload: object, name: str
) -> None:
    client, _ = _make_client(router_config, payload)

    state = client.get_object_group(name)

    assert state.name == name
    assert state.exists is False
    assert state.entries == ()


@pytest.mark.parametrize(
    ("payload", "expected_enabled"),
    [
        ([{"show": {"dns-proxy": {"proxy-status": True}}}], True),
        ([{"show": {"dns-proxy": {"proxy-status": "enabled"}}}], True),
        ([{"show": {"dns-proxy": {"enabled": "yes"}}}], True),
        ([{"show": {"dns-proxy": {"enable": "off"}}}], False),
    ],
)
def test_get_dns_proxy_status_parses_boolean_like_payloads(
    router_config, payload: object, expected_enabled: bool
) -> None:
    client, opener = _make_client(router_config, payload)

    status = client.get_dns_proxy_status()

    assert status.enabled is expected_enabled
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [{"show": {"dns-proxy": {}}}]


def test_get_route_binding_parses_list_payload_for_interface_route(router_config) -> None:
    payload = [
        {
            "show": {
                "sc": {
                    "dns-proxy": {
                        "route": {
                            "object-group": [
                                {
                                    "object-group": "svc-other",
                                    "interface": "Other0",
                                },
                                {
                                    "object-group": "svc-telegram",
                                    "interface": "Wireguard0",
                                    "auto": "yes",
                                    "reject": "no",
                                },
                            ]
                        }
                    }
                }
            }
        }
    ]
    client, opener = _make_client(router_config, payload)

    state = client.get_route_binding("svc-telegram")

    assert state.object_group_name == "svc-telegram"
    assert state.exists is True
    assert state.route_target_type == "interface"
    assert state.route_target_value == "Wireguard0"
    assert state.route_interface is None
    assert state.auto is True
    assert state.exclusive is False
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {"show": {"sc": {"dns-proxy": {}}}}
    ]


def test_get_route_binding_parses_flat_route_list_payload(router_config) -> None:
    payload = [
        {
            "show": {
                "sc": {
                    "dns-proxy": {
                        "route": [
                            {
                                "group": "svc-other",
                                "interface": "Other0",
                            },
                            {
                                "group": "svc-telegram",
                                "interface": "Wireguard1",
                                "auto": True,
                                "reject": True,
                            },
                        ]
                    }
                }
            }
        }
    ]
    client, _ = _make_client(router_config, payload)

    state = client.get_route_binding("svc-telegram")

    assert state.object_group_name == "svc-telegram"
    assert state.exists is True
    assert state.route_target_type == "interface"
    assert state.route_target_value == "Wireguard1"
    assert state.route_interface is None
    assert state.auto is True
    assert state.exclusive is True


def test_get_route_binding_parses_dict_payload_for_gateway_route(router_config) -> None:
    payload = [
        {
            "show": {
                "sc": {
                    "dns-proxy": {
                        "route": {
                            "object-group": {
                                "svc-telegram": {
                                    "type": "gateway",
                                    "target": "10.1.111.12",
                                    "interface": "Wireguard0",
                                    "auto": True,
                                    "reject": True,
                                }
                            }
                        }
                    }
                }
            }
        }
    ]
    client, _ = _make_client(router_config, payload)

    state = client.get_route_binding("svc-telegram")

    assert state.exists is True
    assert state.route_target_type == "gateway"
    assert state.route_target_value == "10.1.111.12"
    assert state.route_interface == "Wireguard0"
    assert state.auto is True
    assert state.exclusive is True


def test_get_route_binding_reports_absent_binding_when_route_is_missing(router_config) -> None:
    payload = [{"show": {"sc": {"dns-proxy": {"proxy-status": True}}}}]
    client, _ = _make_client(router_config, payload)

    state = client.get_route_binding("svc-telegram")

    assert state.object_group_name == "svc-telegram"
    assert state.exists is False


@pytest.mark.parametrize(
    ("payload", "expected_candidates"),
    [
        (
            [
                {
                    "show": {
                        "interface": {
                            "Wireguard2": {
                                "id": "Wireguard2",
                                "type": "Wireguard",
                                "description": "Backup tunnel",
                                "interface-name": "Wireguard2",
                                "link": "down",
                                "connected": "no",
                                "state": "down",
                            },
                            "Ethernet0": {
                                "id": "Ethernet0",
                                "type": "ethernet",
                                "description": "Uplink",
                                "interface-name": "Ethernet0",
                                "link": "up",
                                "connected": "yes",
                                "state": "up",
                            },
                            "Wireguard0": {
                                "id": "Wireguard0",
                                "type": "Wireguard",
                                "description": "Primary tunnel",
                                "interface-name": "Wireguard0",
                                "link": "connected",
                                "connected": True,
                                "state": "connected",
                            },
                        }
                    }
                }
            ],
            (
                RouteTargetCandidate(
                    value="Wireguard0",
                    display_name="Wireguard0",
                    status="connected",
                    detail="type=Wireguard, Primary tunnel",
                    connected=True,
                ),
                RouteTargetCandidate(
                    value="Wireguard2",
                    display_name="Wireguard2",
                    status="down",
                    detail="type=Wireguard, Backup tunnel",
                    connected=False,
                ),
            ),
        ),
        (
            [
                {
                    "show": {
                        "interface": {
                            "interface": [
                                {
                                    "id": "Wireguard3",
                                    "type": "Wireguard",
                                    "description": "Nested tunnel",
                                    "interface-name": "Wireguard3",
                                    "link": "up",
                                    "connected": "connected",
                                    "state": "up",
                                },
                                {
                                    "id": "Other0",
                                    "type": "ethernet",
                                    "description": "Ignore me",
                                    "interface-name": "Other0",
                                    "link": "up",
                                    "connected": "yes",
                                    "state": "up",
                                },
                                {
                                    "id": "Wireguard1",
                                    "type": "Wireguard",
                                    "description": "Another tunnel",
                                    "link": "down",
                                    "connected": False,
                                    "state": "down",
                                },
                            ]
                        }
                    }
                }
            ],
            (
                RouteTargetCandidate(
                    value="Wireguard1",
                    display_name="Wireguard1",
                    status="down",
                    detail="type=Wireguard, Another tunnel",
                    connected=False,
                ),
                RouteTargetCandidate(
                    value="Wireguard3",
                    display_name="Wireguard3",
                    status="up",
                    detail="type=Wireguard, Nested tunnel",
                    connected=True,
                ),
            ),
        ),
        (
            [
                {
                    "show": {
                        "interface": [
                            {
                                "name": "Wireguard0",
                                "class": "Wireguard",
                                "description": "Class-shaped tunnel",
                                "link": "up",
                                "connected": "yes",
                            },
                            {
                                "name": "Bridge0",
                                "class": "Bridge",
                                "description": "Local segment",
                                "link": "up",
                                "connected": "yes",
                            },
                        ]
                    }
                }
            ],
            (
                RouteTargetCandidate(
                    value="Wireguard0",
                    display_name="Wireguard0",
                    status="up",
                    detail="class=Wireguard, Class-shaped tunnel",
                    connected=True,
                ),
            ),
        ),
    ],
)
def test_discover_wireguard_route_targets_parses_wireguard_shapes_deterministically(
    router_config, payload: object, expected_candidates: tuple[RouteTargetCandidate, ...]
) -> None:
    client, opener = _make_client(router_config, payload)

    candidates = client.discover_wireguard_route_targets()

    assert candidates == expected_candidates
    assert len(opener.requests) == 1
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [{"show": {"interface": {}}}]


def test_get_object_group_raises_runtime_error_for_invalid_shape(router_config) -> None:
    payload = [
        {
            "show": {
                "sc": {
                    "object-group": {
                        "fqdn": {
                            "group": [
                                {
                                    "group-name": "svc-telegram",
                                    "entry": ["not-a-dict"],
                                }
                            ]
                        }
                    }
                }
            }
        }
    ]
    client, _ = _make_client(router_config, payload)

    with pytest.raises(
        RuntimeError,
        match=(
            r"Router 'router-1' get_object_group\(svc-telegram\) failed: "
            r"entry item must be an object"
        ),
    ):
        client.get_object_group("svc-telegram")


def test_get_dns_proxy_status_raises_runtime_error_for_invalid_shape(router_config) -> None:
    payload = [{"show": {"dns-proxy": {"proxy-status": 1}}}]
    client, _ = _make_client(router_config, payload)

    with pytest.raises(
        RuntimeError,
        match=(
            r"Router 'router-1' get_dns_proxy_status failed: "
            r"field 'proxy-status' must be boolean-like"
        ),
    ):
        client.get_dns_proxy_status()


def test_get_route_binding_raises_runtime_error_for_multiple_matches(router_config) -> None:
    payload = [
        {
            "show": {
                "sc": {
                    "dns-proxy": {
                        "route": {
                            "object-group": [
                                {
                                    "object-group": "svc-telegram",
                                    "interface": "Wireguard0",
                                },
                                {
                                    "object-group": "svc-telegram",
                                    "interface": "Other0",
                                },
                            ]
                        }
                    }
                }
            }
        }
    ]
    client, _ = _make_client(router_config, payload)

    with pytest.raises(
        RuntimeError,
        match=r"get_route_binding\(svc-telegram\) failed: expected at most one route binding",
    ):
        client.get_route_binding("svc-telegram")


def test_ensure_object_group_posts_single_create_command(router_config) -> None:
    client, opener = _make_client(router_config, {})

    client.ensure_object_group("svc-telegram")

    assert len(opener.requests) == 1
    assert opener.timeouts == [15]
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {"parse": "object-group fqdn svc-telegram"}
    ]


def test_remove_object_group_posts_single_delete_command(router_config) -> None:
    client, opener = _make_client(router_config, {})

    client.remove_object_group("svc-telegram-2")

    assert len(opener.requests) == 1
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {"parse": "no object-group fqdn svc-telegram-2"}
    ]


def test_add_entries_skips_request_for_empty_normalized_input(router_config) -> None:
    client, opener = _make_client(router_config, {})

    client.add_entries("svc-telegram", ["  ", "\t"])

    assert opener.requests == []


def test_add_entries_normalizes_sorts_and_deduplicates_commands(router_config) -> None:
    client, opener = _make_client(router_config, {})

    client.add_entries(
        "svc-telegram",
        [" z.example ", "a.example", "z.example", "b.example"],
    )

    assert len(opener.requests) == 1
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {"parse": "object-group fqdn svc-telegram include a.example"},
        {"parse": "object-group fqdn svc-telegram include b.example"},
        {"parse": "object-group fqdn svc-telegram include z.example"},
    ]


def test_remove_entries_batches_commands_in_fixed_chunks(router_config) -> None:
    items = [f"host-{index:03d}.example" for index in range(205)]
    client, opener = _make_client(router_config, ({}, {}))

    client.remove_entries("svc-telegram", items)

    assert len(opener.requests) == 2
    first_batch = json.loads(opener.requests[0].data.decode("utf-8"))
    second_batch = json.loads(opener.requests[1].data.decode("utf-8"))
    assert len(first_batch) == 200
    assert len(second_batch) == 5
    assert first_batch[0] == {"parse": "no object-group fqdn svc-telegram include host-000.example"}
    assert second_batch[-1] == {
        "parse": "no object-group fqdn svc-telegram include host-204.example"
    }


def test_save_config_posts_dedicated_save_command(router_config) -> None:
    client, opener = _make_client(router_config, {})

    client.save_config()

    assert len(opener.requests) == 1
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {"parse": "system configuration save"}
    ]


def test_get_static_routes_parses_list_payload_with_comments(router_config) -> None:
    payload = (
        [
            {
                "show": {
                    "sc": {
                        "ip": {
                            "route": [
                                {
                                    "network": "149.154.160.0",
                                    "mask": "255.255.240.0",
                                    "interface": "Wireguard0",
                                    "auto": True,
                                    "reject": True,
                                    "comment": "fqdn-updater:telegram Telegram",
                                },
                                {
                                    "network": "203.0.113.0",
                                    "mask": "255.255.255.0",
                                    "interface": "ISP",
                                },
                            ]
                        }
                    }
                }
            }
        ],
        [
            {
                "show": {
                    "sc": {
                        "ipv6": {
                            "route": [
                                {
                                    "prefix": "2001:67c:4e8::/48",
                                    "interface": "Wireguard0",
                                    "comment": "fqdn-updater:telegram Telegram",
                                }
                            ]
                        }
                    }
                }
            }
        ],
    )
    client, opener = _make_client(router_config, payload)

    routes = client.get_static_routes()

    assert [json.loads(request.data.decode("utf-8")) for request in opener.requests] == [
        [{"show": {"sc": {"ip": {"route": {}}}}}],
        [{"show": {"sc": {"ipv6": {"route": {}}}}}],
    ]
    assert routes == (
        StaticRouteState(
            network="149.154.160.0/20",
            route_target_type="interface",
            route_target_value="Wireguard0",
            auto=True,
            exclusive=True,
            comment="fqdn-updater:telegram Telegram",
        ),
        StaticRouteState(
            network="203.0.113.0/24",
            route_target_type="interface",
            route_target_value="ISP",
        ),
        StaticRouteState(
            network="2001:67c:4e8::/48",
            route_target_type="interface",
            route_target_value="Wireguard0",
            comment="fqdn-updater:telegram Telegram",
        ),
    )


def test_get_static_routes_parses_config_payload_with_comments(router_config) -> None:
    payload = (
        [
            {
                "show": {
                    "sc": {
                        "ip": {
                            "route": {
                                "telegram": {
                                    "network": "149.154.160.0",
                                    "mask": "255.255.240.0",
                                    "interface": "Wireguard0",
                                    "comment": "fqdn-updater:telegram Telegram",
                                }
                            }
                        }
                    }
                }
            }
        ],
        [{"show": {"sc": {"ipv6": {}}}}],
    )
    client, _ = _make_client(router_config, payload)

    routes = client.get_static_routes()

    assert routes == (
        StaticRouteState(
            network="149.154.160.0/20",
            route_target_type="interface",
            route_target_value="Wireguard0",
            comment="fqdn-updater:telegram Telegram",
        ),
    )


def test_ensure_route_posts_single_managed_command(router_config) -> None:
    client, opener = _make_client(router_config, {})

    client.ensure_route(
        RouteBindingSpec(
            object_group_name="svc-telegram",
            route_target_type="gateway",
            route_target_value="10.1.111.12",
            route_interface="Wireguard0",
            auto=True,
            exclusive=True,
        )
    )

    assert len(opener.requests) == 1
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {"parse": "dns-proxy route object-group svc-telegram 10.1.111.12 Wireguard0 auto reject"}
    ]


def test_remove_route_posts_single_managed_command(router_config) -> None:
    client, opener = _make_client(router_config, {})

    client.remove_route(
        RouteBindingState(
            object_group_name="svc-telegram-2",
            exists=True,
            route_target_type="interface",
            route_target_value="Wireguard1",
            auto=True,
            exclusive=True,
        )
    )

    assert len(opener.requests) == 1
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {"parse": "no dns-proxy route object-group svc-telegram-2 Wireguard1"}
    ]


def test_remove_route_treats_missing_route_as_noop(router_config) -> None:
    client, opener = _make_client(
        router_config,
        [
            {
                "status": [
                    {"status": "error", "message": 'unable to find a route to "svc-telegram-2"'}
                ]
            }
        ],
    )

    client.remove_route(
        RouteBindingState(
            object_group_name="svc-telegram-2",
            exists=True,
            route_target_type="interface",
            route_target_value="Wireguard1",
            auto=True,
            exclusive=True,
        )
    )

    assert len(opener.requests) == 1


def test_ensure_static_route_posts_structured_ipv4_payload_with_comment_and_flags(
    router_config,
) -> None:
    client, opener = _make_client(router_config, {})

    client.ensure_static_route(
        StaticRouteSpec(
            service_key="telegram",
            network="149.154.160.0/20",
            route_target_type="gateway",
            route_target_value="10.1.111.12",
            route_interface="Wireguard0",
            auto=True,
            exclusive=True,
            comment="fqdn-updater:telegram Telegram",
        )
    )

    assert len(opener.requests) == 1
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {
            "ip": {
                "route": {
                    "network": "149.154.160.0",
                    "mask": "255.255.240.0",
                    "gateway": "10.1.111.12",
                    "interface": "Wireguard0",
                    "auto": True,
                    "reject": True,
                    "comment": "fqdn-updater:telegram Telegram",
                }
            }
        }
    ]


def test_remove_static_route_posts_structured_ipv4_payload_without_auto_or_reject(
    router_config,
) -> None:
    client, opener = _make_client(router_config, {})

    client.remove_static_route(
        StaticRouteState(
            network="149.154.160.0/20",
            route_target_type="gateway",
            route_target_value="10.1.111.12",
            route_interface="Wireguard0",
            comment="fqdn-updater:telegram Telegram",
        )
    )

    assert len(opener.requests) == 1
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {
            "ip": {
                "route": {
                    "network": "149.154.160.0",
                    "mask": "255.255.240.0",
                    "gateway": "10.1.111.12",
                    "interface": "Wireguard0",
                    "comment": "fqdn-updater:telegram Telegram",
                    "no": True,
                }
            }
        }
    ]


def test_ensure_static_route_posts_structured_ipv6_payload_with_comment_and_flags(
    router_config,
) -> None:
    client, opener = _make_client(router_config, {})

    client.ensure_static_route(
        StaticRouteSpec(
            service_key="telegram",
            network="2001:67c:4e8::/48",
            route_target_type="interface",
            route_target_value="Wireguard0",
            auto=True,
            exclusive=True,
            comment="fqdn-updater:telegram Telegram",
        )
    )

    assert len(opener.requests) == 1
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {
            "ipv6": {
                "route": {
                    "prefix": "2001:67c:4e8::/48",
                    "interface": "Wireguard0",
                    "auto": True,
                    "reject": True,
                    "comment": "fqdn-updater:telegram Telegram",
                }
            }
        }
    ]


def test_remove_static_route_posts_structured_ipv6_payload_with_prefix(
    router_config,
) -> None:
    client, opener = _make_client(router_config, {})

    client.remove_static_route(
        StaticRouteState(
            network="2001:67c:4e8::/48",
            route_target_type="interface",
            route_target_value="Wireguard0",
            comment="fqdn-updater:telegram Telegram",
        )
    )

    assert len(opener.requests) == 1
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {
            "ipv6": {
                "route": {
                    "prefix": "2001:67c:4e8::/48",
                    "interface": "Wireguard0",
                    "comment": "fqdn-updater:telegram Telegram",
                    "no": True,
                }
            }
        }
    ]


def test_get_static_routes_refuses_malformed_managed_static_route_payloads(
    router_config,
) -> None:
    payload = (
        [
            {
                "show": {
                    "sc": {
                        "ip": {
                            "route": {
                                "telegram": {
                                    "network": "149.154.160.0",
                                    "mask": "255.255.240.0",
                                    "comment": "fqdn-updater:telegram Telegram",
                                }
                            }
                        }
                    }
                }
            }
        ],
        [{"show": {"sc": {"ipv6": {}}}}],
    )
    client, _ = _make_client(router_config, payload)

    with pytest.raises(RuntimeError, match="managed static route is not parseable"):
        client.get_static_routes()


def test_write_operations_wrap_http_errors_as_runtime_errors(router_config) -> None:
    client = KeeneticRciClientFactory().create(router=router_config, password="secret")

    class _FailingOpener:
        def open(self, http_request, timeout: int) -> _FakeResponse:
            raise error.HTTPError(
                url=http_request.full_url,
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=None,
            )

    client._opener = _FailingOpener()  # type: ignore[attr-defined]

    with pytest.raises(
        RuntimeError,
        match=r"Router 'router-1' add_entries\(svc-telegram\) failed: authentication failed",
    ):
        client.add_entries("svc-telegram", ["a.example"])


def test_request_retries_transient_transport_failures(monkeypatch, router_config) -> None:
    client = KeeneticRciClientFactory().create(router=router_config, password="secret")
    opener = _FlakyTransportOpener(
        failures_before_success=2,
        payload=[{"show": {"dns-proxy": {"proxy-status": True}}}],
    )
    client._opener = opener  # type: ignore[attr-defined]
    sleep_delays: list[float] = []
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_client.time.sleep",
        lambda delay: sleep_delays.append(delay),
    )
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_client.random.uniform",
        lambda start, end: 0.0,  # noqa: ARG005
    )

    status = client.get_dns_proxy_status()

    assert status.enabled is True
    assert len(opener.requests) == 3
    assert opener.timeouts == [15, 15, 15]
    assert sleep_delays == [1.0, 2.0]


def test_request_reports_transport_failure_after_five_attempts(monkeypatch, router_config) -> None:
    client = KeeneticRciClientFactory().create(router=router_config, password="secret")
    opener = _FlakyTransportOpener(
        failures_before_success=5,
        payload=[{"show": {"dns-proxy": {"proxy-status": True}}}],
    )
    client._opener = opener  # type: ignore[attr-defined]
    sleep_delays: list[float] = []
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_client.time.sleep",
        lambda delay: sleep_delays.append(delay),
    )
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_client.random.uniform",
        lambda start, end: 0.0,  # noqa: ARG005
    )

    with pytest.raises(
        RuntimeError,
        match=(
            r"Router 'router-1' get_dns_proxy_status failed: "
            r"transport failed after 5 attempts: temporary TLS failure"
        ),
    ):
        client.get_dns_proxy_status()

    assert len(opener.requests) == 5
    assert opener.timeouts == [15, 15, 15, 15, 15]
    assert sleep_delays == [1.0, 2.0, 4.0, 8.0]


def test_request_reports_tls_diagnostics_for_certificate_failures(
    monkeypatch,
    router_config,
) -> None:
    client = KeeneticRciClientFactory().create(router=router_config, password="secret")

    class _CertificateFailingOpener:
        requests: list[object]
        timeouts: list[int]

        def __init__(self) -> None:
            self.requests = []
            self.timeouts = []

        def open(self, http_request, timeout: int) -> _FakeResponse:
            self.requests.append(http_request)
            self.timeouts.append(timeout)
            raise error.URLError(
                ssl.SSLCertVerificationError("certificate verify failed: Hostname mismatch")
            )

    opener = _CertificateFailingOpener()
    client._opener = opener  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_client.time.sleep",
        lambda delay: None,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_client.random.uniform",
        lambda start, end: 0.0,  # noqa: ARG005
    )

    monkeypatch.setattr(
        "fqdn_updater.infrastructure.keenetic_rci_client.socket.getaddrinfo",
        lambda host, port, type: (  # noqa: A002, ARG005
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.11", port)),
        ),
    )

    def fake_probe_tls_endpoint(
        *,
        host: str,
        ip: str,
        port: int,
        timeout: int,
        family_name: str,
    ) -> str:
        return (
            f"tls_probe {family_name}/{ip}:{port} verify=failed "
            f"error=certificate mismatch for {host} timeout={timeout} "
            "cert=subject=wrong.example issuer=Test CA san=wrong.example"
        )

    monkeypatch.setattr(client, "_probe_tls_endpoint", fake_probe_tls_endpoint)

    with pytest.raises(RuntimeError) as exc_info:
        client.get_dns_proxy_status()

    message = str(exc_info.value)
    assert "transport failed after 5 attempts" in message
    assert "certificate verify failed: Hostname mismatch" in message
    assert "attempt_errors=1:SSLCertVerificationError:" in message
    assert "tls_diagnostics host=router-1.example port=443 sni=router-1.example" in message
    assert "resolved_endpoints=ipv4/203.0.113.10:443,ipv4/203.0.113.11:443" in message
    assert "tls_probe ipv4/203.0.113.10:443 verify=failed" in message
    assert "cert=subject=wrong.example issuer=Test CA san=wrong.example" in message
    assert len(opener.requests) == 5
    assert opener.timeouts == [15, 15, 15, 15, 15]


def test_write_operations_raise_runtime_error_for_rci_status_errors(router_config) -> None:
    payload = [
        {
            "parse": {
                "status": [
                    {
                        "status": "error",
                        "code": "1179781",
                        "ident": "Core::Configurator",
                        "message": "not found: command.",
                    }
                ]
            }
        }
    ]
    client, _ = _make_client(router_config, payload)

    with pytest.raises(
        RuntimeError,
        match=(
            r"Router 'router-1' ensure_object_group\(svc-telegram\) failed: "
            r"Core::Configurator - 1179781 - not found: command\."
        ),
    ):
        client.ensure_object_group("svc-telegram")


@pytest.fixture
def router_config():
    from fqdn_updater.domain.config_schema import RouterConfig

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
