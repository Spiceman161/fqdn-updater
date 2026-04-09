from __future__ import annotations

import json
from dataclasses import dataclass
from urllib import error

import pytest

from fqdn_updater.domain.keenetic import RouteBindingSpec
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
                                        {"type": "config", "fqdn": "b.example"},
                                        {"type": "config", "address": "a.example"},
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
    assert state.entries == ("a.example", "b.example")
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
    client, opener = _make_client(router_config, payload)

    state = client.get_object_group("svc-telegram")

    assert state.name == "svc-telegram"
    assert state.exists is True
    assert state.entries == ("a.example", "b.example")
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
        ([{"show": {"sc": {"dns-proxy": {"proxy-status": True}}}}], True),
        ([{"show": {"sc": {"dns-proxy": {"proxy-status": "enabled"}}}}], True),
        ([{"show": {"sc": {"dns-proxy": {"enabled": "yes"}}}}], True),
        ([{"show": {"sc": {"dns-proxy": {"enable": "off"}}}}], False),
    ],
)
def test_get_dns_proxy_status_parses_boolean_like_payloads(
    router_config, payload: object, expected_enabled: bool
) -> None:
    client, _ = _make_client(router_config, payload)

    status = client.get_dns_proxy_status()

    assert status.enabled is expected_enabled


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
    payload = [{"show": {"sc": {"dns-proxy": {"proxy-status": 1}}}}]
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
        {"set": {"object-group": {"fqdn": {"svc-telegram": {}}}}}
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
        {
            "set": {
                "object-group": {"fqdn": {"svc-telegram": {"include": {"address": "a.example"}}}}
            }
        },
        {
            "set": {
                "object-group": {"fqdn": {"svc-telegram": {"include": {"address": "b.example"}}}}
            }
        },
        {
            "set": {
                "object-group": {"fqdn": {"svc-telegram": {"include": {"address": "z.example"}}}}
            }
        },
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
    assert first_batch[0] == {
        "delete": {
            "object-group": {"fqdn": {"svc-telegram": {"include": {"address": "host-000.example"}}}}
        }
    }
    assert second_batch[-1] == {
        "delete": {
            "object-group": {"fqdn": {"svc-telegram": {"include": {"address": "host-204.example"}}}}
        }
    }


def test_save_config_posts_dedicated_save_command(router_config) -> None:
    client, opener = _make_client(router_config, {})

    client.save_config()

    assert len(opener.requests) == 1
    assert json.loads(opener.requests[0].data.decode("utf-8")) == [
        {"system": {"configuration": {"save": {}}}}
    ]


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
        {
            "set": {
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
    ]


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
