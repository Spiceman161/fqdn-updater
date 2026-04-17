from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

import fqdn_updater.cli.panel as panel_module
from fqdn_updater.application.route_target_discovery import RouteTargetDiscoveryResult
from fqdn_updater.cli.panel import PanelController
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig
from fqdn_updater.domain.keenetic import RouteTargetCandidate
from fqdn_updater.infrastructure.secret_env_file import SecretEnvFile


class _FakeDiscoveryService:
    def __init__(self, result: RouteTargetDiscoveryResult) -> None:
        self.result = result
        self.calls: list[str] = []

    def discover_wireguard_targets(self, *, router: RouterConfig) -> RouteTargetDiscoveryResult:
        self.calls.append(router.id)
        return self.result


class _RecordingDiscoveryService:
    def __init__(self, result: RouteTargetDiscoveryResult) -> None:
        self.result = result
        self.routers: list[RouterConfig] = []

    def discover_wireguard_targets(self, *, router: RouterConfig) -> RouteTargetDiscoveryResult:
        self.routers.append(router)
        return self.result


def test_discover_route_targets_returns_empty_candidates_on_failure(tmp_path) -> None:
    controller = _panel_controller(tmp_path)
    router = _router_config()
    config = _app_config(tmp_path)
    fake_service = _FakeDiscoveryService(
        RouteTargetDiscoveryResult(router_id=router.id, error_message="temporary failure")
    )
    controller._route_target_discovery_service = fake_service  # type: ignore[attr-defined]

    candidates = controller._discover_route_targets(config=config, router=router)

    assert candidates == ()
    assert fake_service.calls == [router.id]


def test_discover_route_targets_reports_new_router_missing_secret_as_skipped(tmp_path) -> None:
    console = Console(force_terminal=True, record=True)
    controller = PanelController(
        config_path=tmp_path / "config.json",
        console=console,
    )
    router = _router_config()
    config = _app_config(tmp_path)
    fake_service = _FakeDiscoveryService(
        RouteTargetDiscoveryResult(
            router_id=router.id,
            error_message="Router 'router-1' password env 'ROUTER_ONE_PASSWORD' is not set",
        )
    )
    controller._route_target_discovery_service = fake_service  # type: ignore[attr-defined]

    candidates = controller._discover_route_targets(
        config=config,
        router=router,
        missing_secret_message="WireGuard interface discovery skipped for new router.",
    )

    assert candidates == ()
    assert fake_service.calls == [router.id]
    assert "WireGuard interface discovery skipped for new router." in console.export_text()
    assert "WireGuard interface discovery failed" not in console.export_text()


def test_discover_route_targets_returns_empty_candidates_when_result_is_empty(tmp_path) -> None:
    controller = _panel_controller(tmp_path)
    router = _router_config()
    config = _app_config(tmp_path)
    fake_service = _FakeDiscoveryService(
        RouteTargetDiscoveryResult(router_id=router.id, candidates=())
    )
    controller._route_target_discovery_service = fake_service  # type: ignore[attr-defined]

    candidates = controller._discover_route_targets(config=config, router=router)

    assert candidates == ()
    assert fake_service.calls == [router.id]


def test_discover_route_targets_returns_candidates_when_discovery_succeeds(tmp_path) -> None:
    controller = _panel_controller(tmp_path)
    router = _router_config()
    config = _app_config(tmp_path)
    candidates = (
        RouteTargetCandidate(
            value="Wireguard2",
            display_name="Wireguard2",
            status="connected",
            detail="type=Wireguard, Primary tunnel",
            connected=True,
        ),
        RouteTargetCandidate(
            value="Wireguard0",
            display_name="Wireguard0",
            status="up",
            detail="type=Wireguard, Backup tunnel",
            connected=False,
        ),
    )
    fake_service = _FakeDiscoveryService(
        RouteTargetDiscoveryResult(router_id=router.id, candidates=candidates)
    )
    controller._route_target_discovery_service = fake_service  # type: ignore[attr-defined]

    discovered_candidates = controller._discover_route_targets(config=config, router=router)

    assert discovered_candidates == candidates
    assert fake_service.calls == [router.id]


def test_build_mappings_falls_back_to_wireguard0_without_discovered_candidates(
    tmp_path,
    monkeypatch,
) -> None:
    controller = _panel_controller(tmp_path)
    controller._prompt_route_interface = lambda *, label, candidates, default: default  # type: ignore[method-assign]
    monkeypatch.setattr(panel_module.Confirm, "ask", lambda *args, **kwargs: False)

    mappings = controller._build_mappings(
        router_id="router-1",
        selected_services={"telegram", "google_ai"},
        route_target_candidates=(),
    )

    assert mappings == [
        {
            "router_id": "router-1",
            "service_key": "google_ai",
            "object_group_name": "fqdn-google_ai",
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "route_interface": None,
            "exclusive": True,
            "auto": True,
            "managed": True,
        },
        {
            "router_id": "router-1",
            "service_key": "telegram",
            "object_group_name": "fqdn-telegram",
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "route_interface": None,
            "exclusive": True,
            "auto": True,
            "managed": True,
        },
    ]


def test_build_mappings_uses_discovered_candidates_for_default_and_google_ai_interfaces(
    tmp_path,
    monkeypatch,
) -> None:
    controller = _panel_controller(tmp_path)
    prompt_calls: list[tuple[str, str]] = []

    def _prompt_route_interface(*, label: str, candidates, default: str) -> str:
        prompt_calls.append((label, default))
        if label == "Default Keenetic route interface":
            return "Wireguard2"
        if label == "google_ai route interface":
            return "Wireguard0"
        raise AssertionError(f"unexpected label: {label}")

    controller._prompt_route_interface = _prompt_route_interface  # type: ignore[method-assign]
    monkeypatch.setattr(panel_module.Confirm, "ask", lambda *args, **kwargs: True)

    mappings = controller._build_mappings(
        router_id="router-1",
        selected_services={"telegram", "google_ai", "youtube"},
        route_target_candidates=(
            RouteTargetCandidate(
                value="Wireguard2",
                display_name="Wireguard2",
                status="connected",
                detail="type=Wireguard, Primary tunnel",
                connected=True,
            ),
            RouteTargetCandidate(
                value="Wireguard0",
                display_name="Wireguard0",
                status="up",
                detail="type=Wireguard, Backup tunnel",
                connected=False,
            ),
        ),
    )

    assert prompt_calls == [
        ("Default Keenetic route interface", "Wireguard0"),
        ("google_ai route interface", "Wireguard2"),
    ]
    assert mappings == [
        {
            "router_id": "router-1",
            "service_key": "google_ai",
            "object_group_name": "fqdn-google_ai",
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "route_interface": None,
            "exclusive": True,
            "auto": True,
            "managed": True,
        },
        {
            "router_id": "router-1",
            "service_key": "telegram",
            "object_group_name": "fqdn-telegram",
            "route_target_type": "interface",
            "route_target_value": "Wireguard2",
            "route_interface": None,
            "exclusive": True,
            "auto": True,
            "managed": True,
        },
        {
            "router_id": "router-1",
            "service_key": "youtube",
            "object_group_name": "fqdn-youtube",
            "route_target_type": "interface",
            "route_target_value": "Wireguard2",
            "route_interface": None,
            "exclusive": True,
            "auto": True,
            "managed": True,
        },
    ]


def test_add_new_router_uses_draft_router_for_route_target_discovery(
    tmp_path,
    monkeypatch,
) -> None:
    controller = _panel_controller(tmp_path)
    _write_panel_config(controller._config_path)
    generated_password = "Aa1!bcdefghijklmnopq"
    password_env = "FQDN_UPDATER_ROUTER_ROUTER_1_PASSWORD"
    SecretEnvFile(path=tmp_path / ".env.secrets").write_value(
        key=password_env,
        value="existing-router-secret",
    )
    fake_service = _RecordingDiscoveryService(
        RouteTargetDiscoveryResult(
            router_id="router-1",
            candidates=(
                RouteTargetCandidate(
                    value="Wireguard9",
                    display_name="Wireguard9",
                    status="up",
                    detail="class=Wireguard",
                    connected=True,
                ),
            ),
        )
    )
    controller._route_target_discovery_service = fake_service  # type: ignore[attr-defined]
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator,
        "generate",
        lambda self: generated_password,
    )

    prompt_answers = iter(
        (
            "router-1",
            "Router 1",
            "http://router-1.example",
            "api-user",
            "15",
            "",
            "1",
            "",
        )
    )
    monkeypatch.setattr(
        panel_module.Prompt,
        "ask",
        lambda *args, **kwargs: next(prompt_answers),
    )
    monkeypatch.setattr(panel_module.Confirm, "ask", lambda *args, **kwargs: True)

    controller._add_or_replace_router(initial_router_id=None)

    assert len(fake_service.routers) == 1
    draft_router = fake_service.routers[0]
    assert draft_router.id == "router-1"
    assert draft_router.name == "Router 1"
    assert str(draft_router.rci_url) == "https://router-1.example/rci/"
    assert draft_router.username == "api-user"
    assert draft_router.password_env == password_env
    assert draft_router.timeout_seconds == 15

    payload = json.loads(controller._config_path.read_text(encoding="utf-8"))
    assert payload["routers"][0]["rci_url"] == "https://router-1.example/rci/"
    assert payload["mappings"] == [
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-telegram",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard9",
            "router_id": "router-1",
            "service_key": "telegram",
        }
    ]
    assert SecretEnvFile(path=tmp_path / ".env.secrets").read()[password_env] == generated_password


def test_add_new_router_reports_invalid_draft_router_without_traceback(
    tmp_path,
    monkeypatch,
) -> None:
    controller = _panel_controller(tmp_path)
    _write_panel_config(controller._config_path)
    fake_service = _RecordingDiscoveryService(
        RouteTargetDiscoveryResult(router_id="router-1", candidates=())
    )
    controller._route_target_discovery_service = fake_service  # type: ignore[attr-defined]

    def _unexpected_generate(self):
        raise AssertionError("password generation should not run for invalid router input")

    monkeypatch.setattr(panel_module.RciPasswordGenerator, "generate", _unexpected_generate)
    prompt_answers = iter(
        (
            "router-1",
            "Router 1",
            "https://router-1.example/api/",
            "api-user",
            "15",
            "",
        )
    )
    monkeypatch.setattr(
        panel_module.Prompt,
        "ask",
        lambda *args, **kwargs: next(prompt_answers),
    )

    controller._add_or_replace_router(initial_router_id=None)

    assert fake_service.routers == []
    assert json.loads(controller._config_path.read_text(encoding="utf-8"))["routers"] == []
    assert not (tmp_path / ".env.secrets").exists()


def _panel_controller(tmp_path: Path) -> PanelController:
    return PanelController(
        config_path=tmp_path / "config.json",
        console=Console(force_terminal=True),
    )


def _app_config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "routers": [],
            "services": [],
            "mappings": [],
            "runtime": {"secrets_env_file": str(tmp_path / ".env.secrets")},
        }
    )


def _router_config() -> RouterConfig:
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


def _write_panel_config(path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "routers": [],
            "services": [
                {
                    "key": "telegram",
                    "source_urls": ["https://example.com/telegram.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                }
            ],
            "mappings": [],
            "runtime": {"secrets_env_file": ".env.secrets"},
        }
    )
    path.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
