from __future__ import annotations

import json

import pytest

import fqdn_updater.cli.panel as panel_module
from fqdn_updater.application.route_target_discovery import RouteTargetDiscoveryResult
from fqdn_updater.domain.keenetic import RouteTargetCandidate
from fqdn_updater.infrastructure.secret_env_file import (
    SecretEnvFile,
    password_env_key_for_router_id,
)

from .panel_test_support import ScriptedPromptAdapter, make_panel_controller, write_config


class _FakeDiscoveryService:
    def __init__(self, result: RouteTargetDiscoveryResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def discover_wireguard_targets(self, *, router) -> RouteTargetDiscoveryResult:
        self.calls.append(router.id)
        return self.result


def test_main_menu_passes_dashboard_hint_lines_to_prompt(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["exit"])
    controller, console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)

    controller.run()

    output = console.export_text()
    assert "Подсказка" not in output
    assert prompts.select_calls[0]["hint_lines"] == panel_module.MAIN_MENU_HINT_LINES


def test_router_menu_passes_hint_lines_to_prompt(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["back"])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
    )

    controller._router_menu()

    assert prompts.select_calls[0]["message"] == "Маршрутизаторы"
    assert prompts.select_calls[0]["hint_lines"] == panel_module.ROUTER_MENU_HINT_LINES


def test_add_router_passes_hint_lines_through_wizard_steps(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Router 1",
            "https://router-1.example/rci/",
            "api_updater",
            "10",
            "Wireguard0",
        ],
        checkbox_answers=[["telegram", "google_ai"]],
        select_answers=["interface"],
        confirm_answers=[False, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    generated_password = "Aa1!bcdefghijklmnopq"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._add_router()

    assert prompts.text_calls[0]["hint_lines"] == panel_module.ADD_ROUTER_HINT_LINES
    assert prompts.text_calls[1]["hint_lines"] == panel_module.ADD_ROUTER_HINT_LINES
    assert prompts.text_calls[2]["hint_lines"] == panel_module.ADD_ROUTER_HINT_LINES
    assert prompts.text_calls[3]["hint_lines"] == panel_module.ADD_ROUTER_HINT_LINES
    assert prompts.checkbox_calls[0]["hint_lines"] == panel_module.ADD_ROUTER_HINT_LINES
    assert prompts.select_calls[0]["hint_lines"] == panel_module.ADD_ROUTER_HINT_LINES
    assert prompts.confirm_calls[-1]["hint_lines"] == panel_module.ADD_ROUTER_SAVE_HINT_LINES


def test_add_router_creates_config_secret_and_default_mappings(tmp_path, monkeypatch) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Router 1",
            "https://router-1.example/rci/",
            "api_updater",
            "10",
            "Wireguard0",
        ],
        checkbox_answers=[["telegram", "google_ai"]],
        select_answers=["interface"],
        confirm_answers=[False, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    generated_password = "Aa1!bcdefghijklmnopq"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._add_router()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"] == [
        {
            "allowed_source_ips": [],
            "auth_method": "digest",
            "enabled": True,
            "id": "router-1",
            "name": "Router 1",
            "password_env": "FQDN_UPDATER_ROUTER_ROUTER_1_PASSWORD",
            "password_file": None,
            "rci_url": "https://router-1.example/rci/",
            "tags": [],
            "timeout_seconds": 10,
            "username": "api_updater",
        }
    ]
    assert payload["mappings"] == [
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-google_ai",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "router_id": "router-1",
            "service_key": "google_ai",
        },
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-telegram",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "router_id": "router-1",
            "service_key": "telegram",
        },
    ]
    secret_env = SecretEnvFile(path=tmp_path / ".env.secrets")
    assert secret_env.read() == {
        "FQDN_UPDATER_ROUTER_ROUTER_1_PASSWORD": generated_password,
    }
    assert generated_password not in controller._config_path.read_text(encoding="utf-8")


def test_add_router_generates_transliterated_id_from_cyrillic_name(tmp_path, monkeypatch) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Тестовый маршрутизатор",
            "https://main.example/rci/",
            "api-user",
            "10",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    monkeypatch.setattr(panel_module.RciPasswordGenerator, "generate", lambda self: "unused")

    controller._add_router()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"][-1]["id"] == "testovyi-marshrutizator"
    assert payload["routers"][-1]["password_env"] == (
        "FQDN_UPDATER_ROUTER_TESTOVYI_MARSHRUTIZATOR_PASSWORD"
    )


def test_edit_router_preserves_existing_mappings_and_secret_reference(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["router-1"],
        text_answers=[
            "Router One Renamed",
            "https://router-1-renamed.example/rci/",
            "api_updater",
            "15",
        ],
        confirm_answers=[True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
        mappings=[
            {
                "router_id": "router-1",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            }
        ],
    )

    controller._edit_router()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"][0]["name"] == "Router One Renamed"
    assert payload["routers"][0]["password_env"] == "ROUTER_ONE_SECRET"
    assert payload["mappings"] == [
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-telegram",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "router_id": "router-1",
            "service_key": "telegram",
        }
    ]


def test_toggle_router_enabled_preserves_existing_mappings(tmp_path) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["router-1"], confirm_answers=[True])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
        mappings=[
            {
                "router_id": "router-1",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            }
        ],
    )

    controller._toggle_router_enabled()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"][0]["enabled"] is False
    assert payload["mappings"][0]["service_key"] == "telegram"


def test_lists_menu_updates_services_and_route_targets_preserving_disabled_mappings(
    tmp_path,
) -> None:
    prompts = ScriptedPromptAdapter(
        select_answers=["router-1", "gateway", "interface", "Wireguard7"],
        checkbox_answers=[["telegram", "google_ai", "youtube"]],
        text_answers=["10.0.0.2", "Wireguard9"],
        confirm_answers=[True, True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
        services=[
            {
                "key": "telegram",
                "source_urls": ["https://example.com/telegram.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            },
            {
                "key": "google_ai",
                "source_urls": ["https://example.com/google-ai.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            },
            {
                "key": "youtube",
                "source_urls": ["https://example.com/youtube.lst"],
                "format": "raw_domain_list",
                "enabled": True,
            },
            {
                "key": "discord",
                "source_urls": ["https://example.com/discord.lst"],
                "format": "raw_domain_list",
                "enabled": False,
            },
        ],
        mappings=[
            {
                "router_id": "router-1",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            },
            {
                "router_id": "router-1",
                "service_key": "google_ai",
                "object_group_name": "fqdn-google_ai",
                "route_target_type": "interface",
                "route_target_value": "Wireguard1",
                "managed": True,
            },
            {
                "router_id": "router-1",
                "service_key": "discord",
                "object_group_name": "fqdn-discord",
                "route_target_type": "interface",
                "route_target_value": "Wireguard3",
                "managed": True,
            },
        ],
    )
    SecretEnvFile(path=tmp_path / ".env.secrets").write_value(
        key="ROUTER_ONE_SECRET",
        value="existing-secret",
    )
    controller._route_target_discovery_service = _FakeDiscoveryService(  # type: ignore[attr-defined]
        RouteTargetDiscoveryResult(
            router_id="router-1",
            candidates=(
                RouteTargetCandidate(
                    value="Wireguard7",
                    display_name="Wireguard7",
                    status="up",
                    detail="type=Wireguard",
                    connected=True,
                ),
            ),
        )
    )

    controller._lists_menu()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    mappings = sorted(payload["mappings"], key=lambda item: item["service_key"])
    assert mappings == [
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-discord",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard3",
            "router_id": "router-1",
            "service_key": "discord",
        },
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-google_ai",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard7",
            "router_id": "router-1",
            "service_key": "google_ai",
        },
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-telegram",
            "route_interface": "Wireguard9",
            "route_target_type": "gateway",
            "route_target_value": "10.0.0.2",
            "router_id": "router-1",
            "service_key": "telegram",
        },
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-youtube",
            "route_interface": "Wireguard9",
            "route_target_type": "gateway",
            "route_target_value": "10.0.0.2",
            "router_id": "router-1",
            "service_key": "youtube",
        },
    ]


def test_rotate_password_reuses_existing_password_env_and_preserves_mappings(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["router-1"], confirm_answers=[True])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-1",
                "name": "Router 1",
                "rci_url": "https://router-1.example/rci/",
                "username": "api-user",
                "password_env": "ROUTER_ONE_SECRET",
                "enabled": True,
            }
        ],
        mappings=[
            {
                "router_id": "router-1",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            }
        ],
    )
    original_config = controller._config_path.read_text(encoding="utf-8")
    secret_path = tmp_path / ".env.secrets"
    secret_path.write_text("ROUTER_ONE_SECRET=old-secret\n", encoding="utf-8")
    generated_password = "Bb2@cdefghijklmnopqr"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._rotate_router_password()

    assert controller._config_path.read_text(encoding="utf-8") == original_config
    assert SecretEnvFile(path=secret_path).read() == {"ROUTER_ONE_SECRET": generated_password}


def test_rotate_password_switches_password_file_to_env_and_clears_password_file(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["router-2"], confirm_answers=[True])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "router-2",
                "name": "Router 2",
                "rci_url": "https://router-2.example/rci/",
                "username": "api-user",
                "password_file": "/run/secrets/router-2",
                "enabled": True,
            }
        ],
        mappings=[
            {
                "router_id": "router-2",
                "service_key": "telegram",
                "object_group_name": "fqdn-telegram",
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
                "managed": True,
            }
        ],
    )
    generated_password = "Cc3#defghijklmnopqrs"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    controller._rotate_router_password()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"] == [
        {
            "allowed_source_ips": [],
            "auth_method": "digest",
            "enabled": True,
            "id": "router-2",
            "name": "Router 2",
            "password_env": "FQDN_UPDATER_ROUTER_ROUTER_2_PASSWORD",
            "password_file": None,
            "rci_url": "https://router-2.example/rci/",
            "tags": [],
            "timeout_seconds": 10,
            "username": "api-user",
        }
    ]
    assert SecretEnvFile(path=tmp_path / ".env.secrets").read() == {
        "FQDN_UPDATER_ROUTER_ROUTER_2_PASSWORD": generated_password,
    }


def test_add_router_auto_generates_unique_id_when_secret_env_would_collide(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Home Router",
            "https://home-router.example/rci/",
            "api-user",
            "10",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    original_password_env = password_env_key_for_router_id("home-router")
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "home_router",
                "name": "Home Router Legacy",
                "rci_url": "https://home-router.example/rci/",
                "username": "api-user",
                "password_env": original_password_env,
                "enabled": True,
            }
        ],
    )
    SecretEnvFile(path=tmp_path / ".env.secrets").write_value(
        key=original_password_env,
        value="old-secret",
    )
    monkeypatch.setattr(panel_module.RciPasswordGenerator, "generate", lambda self: "unused")

    controller._add_router()

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"][-1]["id"] == "home-router-2"
    assert payload["routers"][-1]["password_env"] == "FQDN_UPDATER_ROUTER_HOME_ROUTER_2_PASSWORD"
    assert SecretEnvFile(path=tmp_path / ".env.secrets").read() == {
        "FQDN_UPDATER_ROUTER_HOME_ROUTER_2_PASSWORD": "unused",
        original_password_env: "old-secret",
    }


def test_rotate_password_rejects_deterministic_password_env_collisions_before_secret_write(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["home-router"], confirm_answers=[])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    password_env = password_env_key_for_router_id("home-router")
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "home-router",
                "name": "Home Router",
                "rci_url": "https://home-router.example/rci/",
                "username": "api-user",
                "password_file": "/run/secrets/home-router",
                "enabled": True,
            },
            {
                "id": "home_router",
                "name": "Home Router Alias",
                "rci_url": "https://home-router-alias.example/rci/",
                "username": "api-user",
                "password_env": password_env,
                "enabled": True,
            },
        ],
    )
    SecretEnvFile(path=tmp_path / ".env.secrets").write_value(key=password_env, value="old-secret")
    original_config = controller._config_path.read_text(encoding="utf-8")
    monkeypatch.setattr(panel_module.RciPasswordGenerator, "generate", lambda self: "unused")

    def _unexpected_write_value(*args, **kwargs) -> None:
        raise AssertionError("SecretEnvFile.write_value should not be reached on collision")

    monkeypatch.setattr(SecretEnvFile, "write_value", _unexpected_write_value)

    with pytest.raises(RuntimeError) as exc_info:
        controller._rotate_router_password()

    assert f"Password env '{password_env}' is already used by router 'home_router'" in str(
        exc_info.value
    )
    assert controller._config_path.read_text(encoding="utf-8") == original_config
    assert SecretEnvFile(path=tmp_path / ".env.secrets").read() == {password_env: "old-secret"}


def test_add_router_rolls_back_config_when_secret_write_fails(tmp_path, monkeypatch) -> None:
    prompts = ScriptedPromptAdapter(
        text_answers=[
            "Home Router",
            "https://home-router.example/rci/",
            "api-user",
            "10",
        ],
        checkbox_answers=[[]],
        confirm_answers=[True],
    )
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(controller._config_path)
    original_config = controller._config_path.read_text(encoding="utf-8")
    secret_path = tmp_path / ".env.secrets"
    secret_path.write_text("UNRELATED_SECRET=old-secret\n", encoding="utf-8")
    generated_password = "Ff6*ghijklmnopqrstuv"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    def _failing_write_value(*args, **kwargs) -> None:
        raise RuntimeError("secret write failed")

    monkeypatch.setattr(SecretEnvFile, "write_value", _failing_write_value)

    with pytest.raises(RuntimeError) as exc_info:
        controller._add_router()

    assert "secret write failed" in str(exc_info.value)
    assert controller._config_path.read_text(encoding="utf-8") == original_config
    assert SecretEnvFile(path=secret_path).read() == {"UNRELATED_SECRET": "old-secret"}


def test_rotate_password_rolls_back_password_file_reference_when_secret_write_fails(
    tmp_path,
    monkeypatch,
) -> None:
    prompts = ScriptedPromptAdapter(select_answers=["home-router"], confirm_answers=[True])
    controller, _console = make_panel_controller(tmp_path, prompts=prompts)
    write_config(
        controller._config_path,
        routers=[
            {
                "id": "home-router",
                "name": "Home Router",
                "rci_url": "https://home-router.example/rci/",
                "username": "api-user",
                "password_file": "/run/secrets/home-router",
                "enabled": True,
            }
        ],
    )
    original_config = controller._config_path.read_text(encoding="utf-8")
    secret_path = tmp_path / ".env.secrets"
    secret_path.write_text("UNRELATED_SECRET=old-secret\n", encoding="utf-8")
    generated_password = "Gg7(ghijklmnopqrstuvw"
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    def _failing_write_value(*args, **kwargs) -> None:
        raise RuntimeError("secret write failed")

    monkeypatch.setattr(SecretEnvFile, "write_value", _failing_write_value)

    with pytest.raises(RuntimeError) as exc_info:
        controller._rotate_router_password()

    assert "secret write failed" in str(exc_info.value)
    assert controller._config_path.read_text(encoding="utf-8") == original_config
    assert "/run/secrets/home-router" in controller._config_path.read_text(encoding="utf-8")
    assert SecretEnvFile(path=secret_path).read() == {"UNRELATED_SECRET": "old-secret"}
