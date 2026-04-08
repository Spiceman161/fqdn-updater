from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

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
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.requests: list[object] = []
        self.timeouts: list[int] = []

    def open(self, http_request, timeout: int) -> _FakeResponse:
        self.requests.append(http_request)
        self.timeouts.append(timeout)
        return _FakeResponse(self._payload)


def _make_client(router_config, payload: object) -> tuple[KeeneticRciClient, _FakeOpener]:
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


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("ensure_object_group", ("svc-telegram",)),
        ("add_entries", ("svc-telegram", ["a.example"])),
        ("remove_entries", ("svc-telegram", ["a.example"])),
        ("ensure_route", ({"object_group_name": "svc-telegram"},)),
        ("save_config", ()),
    ],
)
def test_client_write_methods_are_explicitly_unimplemented(
    router_config, method_name: str, args: tuple[object, ...]
) -> None:
    client = KeeneticRciClientFactory().create(router=router_config, password="secret")
    method = getattr(client, method_name)

    with pytest.raises(NotImplementedError, match=rf"KeeneticRciClient\.{method_name}"):
        method(*args)


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
