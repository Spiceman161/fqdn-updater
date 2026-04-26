from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

import fqdn_updater.cli.app as cli_app_module
import fqdn_updater.cli.panel as panel_module
from fqdn_updater.application.dry_run_orchestration import DryRunExecutionResult, DryRunOrchestrator
from fqdn_updater.application.keenetic_client import KeeneticClient, KeeneticClientFactory
from fqdn_updater.application.service_sync_planning import ServiceSyncPlan, ServiceSyncPlanner
from fqdn_updater.application.sync_orchestration import SyncExecutionResult
from fqdn_updater.cli.app import app
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig
from fqdn_updater.domain.keenetic import (
    DnsProxyStatus,
    ObjectGroupState,
    RouteBindingDiff,
    RouteBindingSpec,
    RouteBindingState,
)
from fqdn_updater.domain.object_group_diff import ObjectGroupDiff
from fqdn_updater.domain.run_artifact import (
    RouterResultStatus,
    RouterRunResult,
    RunArtifact,
    RunMode,
    RunStatus,
    RunStep,
    RunTrigger,
    ServiceResultStatus,
    ServiceRunResult,
)
from fqdn_updater.domain.source_loading import NormalizedServiceSource, SourceLoadReport
from fqdn_updater.domain.static_route_diff import StaticRouteSpec, StaticRouteState
from fqdn_updater.domain.status_diagnostics import (
    OverallDiagnosticStatus,
    RouterDiagnosticStatus,
    RouterStatusDiagnostic,
    StatusDiagnosticsResult,
)
from fqdn_updater.infrastructure.run_lock import RunLockError
from fqdn_updater.infrastructure.secret_env_file import (
    SecretEnvFile,
    password_env_key_for_router_id,
)
from fqdn_updater.infrastructure.service_count_cache import CachingSourceLoadingService
from fqdn_updater.infrastructure.systemd_scheduler import SystemdScheduleInstallResult

runner = CliRunner()


def test_root_help_shows_expected_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "init" in result.stdout
    assert "config" in result.stdout
    assert "router" in result.stdout
    assert "mapping" in result.stdout
    assert "schedule" in result.stdout
    assert "dry-run" in result.stdout
    assert "sync" in result.stdout
    assert "status" in result.stdout
    assert "panel" in result.stdout


def test_panel_help_is_available() -> None:
    result = runner.invoke(app, ["panel", "--help"])

    assert result.exit_code == 0
    assert "--config" in result.stdout


def test_schedule_help_is_available() -> None:
    result = runner.invoke(app, ["schedule", "--help"])

    assert result.exit_code == 0
    assert "show" in result.stdout
    assert "install" in result.stdout


def test_panel_command_invokes_controller_with_config_path(tmp_path, monkeypatch) -> None:
    calls: list[Path] = []

    class _RecordingPanelController:
        def __init__(self, *, config_path: Path) -> None:
            calls.append(config_path)

        def run(self) -> None:
            return None

    monkeypatch.setattr(cli_app_module, "PanelController", _RecordingPanelController)

    config_path = tmp_path / "config.json"
    result = runner.invoke(app, ["panel", "--config", str(config_path)])

    assert result.exit_code == 0
    assert calls == [config_path]


def test_schedule_show_outputs_human_and_json(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "routers": [],
                "services": [],
                "mappings": [],
                "runtime": {
                    "schedule": {
                        "mode": "daily",
                        "times": ["03:15", "12:00"],
                        "timezone": "Europe/Moscow",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    human = runner.invoke(app, ["schedule", "show", "--config", str(config_path)])
    json_result = runner.invoke(
        app,
        ["schedule", "show", "--config", str(config_path), "--output", "json"],
    )

    assert human.exit_code == 0
    assert "mode=daily" in human.stdout
    assert "times=03:15, 12:00" in human.stdout
    assert "deployment_root=/opt/fqdn-updater" in human.stdout
    assert json.loads(json_result.stdout)["mode"] == "daily"


def test_schedule_set_daily_persists_times_timezone_and_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    _write_management_config(config_path)

    result = runner.invoke(
        app,
        [
            "schedule",
            "set-daily",
            "--config",
            str(config_path),
            "--time",
            "12:00",
            "--time",
            "03:15",
            "--timezone",
            "Europe/Moscow",
        ],
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert payload["runtime"]["schedule"] == {
        "mode": "daily",
        "times": ["03:15", "12:00"],
        "timezone": "Europe/Moscow",
        "weekdays": [],
        "systemd": {
            "compose_service": "fqdn-updater",
            "deployment_root": "/opt/fqdn-updater",
            "unit_name": "fqdn-updater",
        },
    }


def test_schedule_set_weekly_persists_weekdays_and_preserves_existing_systemd_settings(
    tmp_path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "routers": [],
                "services": [],
                "mappings": [],
                "runtime": {
                    "schedule": {
                        "mode": "disabled",
                        "timezone": "UTC",
                        "times": [],
                        "weekdays": [],
                        "systemd": {
                            "unit_name": "custom-fqdn",
                            "deployment_root": "/srv/fqdn-updater",
                            "compose_service": "sync-job",
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "schedule",
            "set-weekly",
            "--config",
            str(config_path),
            "--day",
            "fri",
            "--day",
            "mon",
            "--time",
            "04:00",
            "--timezone",
            "Europe/Moscow",
        ],
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert payload["runtime"]["schedule"] == {
        "mode": "weekly",
        "times": ["04:00"],
        "timezone": "Europe/Moscow",
        "weekdays": ["mon", "fri"],
        "systemd": {
            "compose_service": "sync-job",
            "deployment_root": "/srv/fqdn-updater",
            "unit_name": "custom-fqdn",
        },
    }


def test_schedule_disable_clears_times_and_weekdays(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "routers": [],
                "services": [],
                "mappings": [],
                "runtime": {
                    "schedule": {
                        "mode": "weekly",
                        "times": ["04:00"],
                        "weekdays": ["mon", "fri"],
                        "timezone": "Europe/Moscow",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["schedule", "disable", "--config", str(config_path)])

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert result.exit_code == 0
    assert payload["runtime"]["schedule"]["mode"] == "disabled"
    assert payload["runtime"]["schedule"]["times"] == []
    assert payload["runtime"]["schedule"]["weekdays"] == []


def test_schedule_install_uses_installer_and_renders_result(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config().model_dump(mode="json")), encoding="utf-8")
    install_calls: list[tuple[AppConfig, Path]] = []

    class _StubInstaller:
        def install(self, *, config: AppConfig, config_path: Path) -> SystemdScheduleInstallResult:
            install_calls.append((config, config_path))
            return SystemdScheduleInstallResult(
                service_path=Path("/etc/systemd/system/fqdn-updater.service"),
                timer_path=Path("/etc/systemd/system/fqdn-updater.timer"),
                timer_action="started",
            )

    monkeypatch.setattr(cli_app_module, "_schedule_installer", lambda: _StubInstaller())

    result = runner.invoke(app, ["schedule", "install", "--config", str(config_path)])

    assert result.exit_code == 0
    assert install_calls[0][1] == config_path
    assert "timer_action=started" in result.stdout
    assert "/etc/systemd/system/fqdn-updater.timer" in result.stdout


def test_dry_run_orchestrator_uses_caching_source_loader(tmp_path) -> None:
    config = _config()

    orchestrator = cli_app_module._dry_run_orchestrator(
        config_path=tmp_path / "config.json",
        config=config,
    )

    assert isinstance(orchestrator, DryRunOrchestrator)
    assert isinstance(orchestrator._source_loader, CachingSourceLoadingService)  # type: ignore[attr-defined]
    assert orchestrator._source_loader._cache_path == tmp_path / "data" / "service-count-cache.json"  # type: ignore[attr-defined]


def test_sync_orchestrator_uses_caching_source_loader(tmp_path) -> None:
    config = _config()

    orchestrator = cli_app_module._sync_orchestrator(
        config_path=tmp_path / "config.json",
        config=config,
    )

    assert isinstance(orchestrator._source_loader, CachingSourceLoadingService)  # type: ignore[attr-defined]
    assert orchestrator._source_loader._cache_path == tmp_path / "data" / "service-count-cache.json"  # type: ignore[attr-defined]


def test_router_add_writes_valid_router_and_leaves_file_unchanged_on_validation_failures(
    tmp_path,
) -> None:
    config_path = tmp_path / "config.json"
    _write_management_config(config_path)

    success = runner.invoke(
        app,
        [
            "router",
            "add",
            "--config",
            str(config_path),
            "--id",
            "router-1",
            "--name",
            "Router 1",
            "--rci-url",
            "https://router-1.example/rci/",
            "--username",
            "api-user",
            "--password-env",
            "ROUTER_ONE_SECRET",
        ],
    )

    assert success.exit_code == 0
    assert "Роутер добавлен: id=router-1" in success.stdout
    valid_contents = config_path.read_text(encoding="utf-8")
    valid_payload = json.loads(valid_contents)
    assert [router["id"] for router in valid_payload["routers"]] == ["router-1"]
    assert valid_payload["routers"][0]["auth_method"] == "digest"
    assert valid_payload["routers"][0]["password_env"] == "ROUTER_ONE_SECRET"
    assert valid_payload["routers"][0]["password_file"] is None

    duplicate = runner.invoke(
        app,
        [
            "router",
            "add",
            "--config",
            str(config_path),
            "--id",
            "router-1",
            "--name",
            "Router 1 duplicate",
            "--rci-url",
            "https://router-1-duplicate.example/rci/",
            "--username",
            "api-user",
            "--password-env",
            "ROUTER_ONE_DUPLICATE_SECRET",
        ],
    )

    assert duplicate.exit_code == 1
    assert "duplicate router id 'router-1'" in duplicate.stderr
    assert config_path.read_text(encoding="utf-8") == valid_contents

    secret_validation = runner.invoke(
        app,
        [
            "router",
            "add",
            "--config",
            str(config_path),
            "--id",
            "router-2",
            "--name",
            "Router 2",
            "--rci-url",
            "https://router-2.example/rci/",
            "--username",
            "api-user",
        ],
    )

    assert secret_validation.exit_code == 1
    assert "must define exactly one of password_env or password_file" in secret_validation.stderr
    assert config_path.read_text(encoding="utf-8") == valid_contents


def test_router_list_outputs_are_deterministic_in_human_and_json(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    _write_management_config(config_path)

    for router_id, name, secret in [
        ("router-1", "Router 1", "ROUTER_ONE_SECRET"),
        ("router-2", "Router 2", "ROUTER_TWO_SECRET"),
    ]:
        result = runner.invoke(
            app,
            [
                "router",
                "add",
                "--config",
                str(config_path),
                "--id",
                router_id,
                "--name",
                name,
                "--rci-url",
                f"https://{router_id}.example/rci/",
                "--username",
                "api-user",
                "--password-env",
                secret,
            ],
        )
        assert result.exit_code == 0

    human = runner.invoke(app, ["router", "list", "--config", str(config_path)])
    json_result = runner.invoke(
        app,
        ["router", "list", "--config", str(config_path), "--output", "json"],
    )

    assert human.exit_code == 0
    assert human.stdout.strip().splitlines() == [
        "Роутеры: count=2",
        "Роутер router-1: enabled=yes rci_url=https://router-1.example/rci/ username=api-user",
        "Роутер router-2: enabled=yes rci_url=https://router-2.example/rci/ username=api-user",
    ]
    assert json_result.exit_code == 0
    assert [router["id"] for router in json.loads(json_result.stdout)] == ["router-1", "router-2"]
    assert (
        json.loads(json_result.stdout)
        == json.loads(config_path.read_text(encoding="utf-8"))["routers"]
    )


def test_router_add_normalizes_copied_http_rci_url_to_https_endpoint(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    _write_management_config(config_path)

    result = runner.invoke(
        app,
        [
            "router",
            "add",
            "--config",
            str(config_path),
            "--id",
            "router-1",
            "--name",
            "Router 1",
            "--rci-url",
            "http://rci.router-1.example",
            "--username",
            "api-user",
            "--password-env",
            "ROUTER_ONE_SECRET",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["routers"][0]["rci_url"] == "https://rci.router-1.example/rci/"


def test_router_list_works_for_readable_only_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    _write_management_config(
        config_path,
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
    config_path.chmod(0o444)

    result = runner.invoke(app, ["router", "list", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "Роутеры: count=1" in result.stdout
    assert (
        "Роутер router-1: enabled=yes rci_url=https://router-1.example/rci/ username=api-user"
        in result.stdout
    )


def test_mapping_set_appends_and_upserts_in_place_and_list_is_deterministic(
    tmp_path,
) -> None:
    config_path = tmp_path / "config.json"
    _write_management_config(
        config_path,
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

    for args in [
        [
            "mapping",
            "set",
            "--config",
            str(config_path),
            "--router-id",
            "router-1",
            "--service-key",
            "telegram",
            "--object-group-name",
            "svc-telegram",
            "--route-target-type",
            "gateway",
            "--route-target-value",
            "10.0.0.1",
        ],
        [
            "mapping",
            "set",
            "--config",
            str(config_path),
            "--router-id",
            "router-1",
            "--service-key",
            "youtube",
            "--object-group-name",
            "svc-youtube",
            "--route-target-type",
            "gateway",
            "--route-target-value",
            "10.0.0.3",
            "--no-auto",
        ],
        [
            "mapping",
            "set",
            "--config",
            str(config_path),
            "--router-id",
            "router-1",
            "--service-key",
            "telegram",
            "--object-group-name",
            "svc-telegram",
            "--route-target-type",
            "gateway",
            "--route-target-value",
            "10.0.0.2",
            "--no-exclusive",
        ],
    ]:
        result = runner.invoke(app, args)
        assert result.exit_code == 0

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert [mapping["service_key"] for mapping in payload["mappings"]] == [
        "telegram",
        "youtube",
    ]
    assert payload["mappings"][0]["route_target_value"] == "10.0.0.2"
    assert payload["mappings"][0]["exclusive"] is False
    assert payload["mappings"][0]["auto"] is True
    assert payload["mappings"][1]["route_target_value"] == "10.0.0.3"
    assert payload["mappings"][1]["auto"] is False
    assert payload["mappings"][1]["exclusive"] is True

    human = runner.invoke(app, ["mapping", "list", "--config", str(config_path)])
    json_result = runner.invoke(
        app,
        ["mapping", "list", "--config", str(config_path), "--output", "json"],
    )

    assert human.exit_code == 0
    assert human.stdout.strip().splitlines() == [
        "Mappings: count=2",
        "Mapping router_id=router-1 service_key=telegram object_group_name=svc-telegram "
        "route_target_type=gateway route_target_value=10.0.0.2",
        "Mapping router_id=router-1 service_key=youtube object_group_name=svc-youtube "
        "route_target_type=gateway route_target_value=10.0.0.3",
    ]
    assert json_result.exit_code == 0
    assert json.loads(json_result.stdout) == payload["mappings"]


@pytest.mark.skip(reason="panel interaction moved to controller-level tests")
def test_config_management_replace_router_preserves_existing_mappings(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    _write_management_config(
        config_path,
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

    result = runner.invoke(
        app,
        [
            "panel",
            "--config",
            str(config_path),
        ],
        input="\n".join(
            (
                "1",
                "router-1",
                "y",
                "Router One Renamed",
                "https://router-1-renamed.example/rci/",
                "api_updater",
                "15",
                "y",
                "",
                "0",
            )
        )
        + "\n",
    )

    assert result.exit_code == 0
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["routers"][0]["name"] == "Router One Renamed"
    assert payload["routers"][0]["password_env"] == "FQDN_UPDATER_ROUTER_ROUTER_1_PASSWORD"
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


@pytest.mark.skip(reason="panel interaction moved to controller-level tests")
def test_panel_creates_config_secret_and_default_mappings(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.json"
    generated_password = "Aa1!bcdefghijklmnopq"

    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    result = runner.invoke(
        app,
        ["panel", "--config", str(config_path)],
        input="\n".join(
            (
                "y",
                "1",
                "router-1",
                "",
                "https://router-1.example/rci/",
                "",
                "",
                "",
                "",
                "",
                "",
                "y",
                "",
                "0",
            )
        )
        + "\n",
    )

    assert result.exit_code == 0
    assert "[x]" in result.stdout
    assert "[ ]" in result.stdout
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["routers"] == [
        {
            "allowed_source_ips": [],
            "auth_method": "digest",
            "enabled": True,
            "id": "router-1",
            "name": "router-1",
            "password_env": "FQDN_UPDATER_ROUTER_ROUTER_1_PASSWORD",
            "password_file": None,
            "rci_url": "https://router-1.example/rci/",
            "tags": [],
            "timeout_seconds": 10,
            "username": "api_updater",
        }
    ]
    assert sorted(mapping["service_key"] for mapping in payload["mappings"]) == [
        "discord",
        "google_ai",
        "meta",
        "telegram",
        "tiktok",
        "twitter",
        "youtube",
    ]
    assert all(mapping["object_group_name"].startswith("fqdn-") for mapping in payload["mappings"])
    assert all(mapping["route_target_value"] == "Wireguard0" for mapping in payload["mappings"])
    secret_env = SecretEnvFile(path=tmp_path / ".env.secrets")
    assert secret_env.read() == {
        "FQDN_UPDATER_ROUTER_ROUTER_1_PASSWORD": generated_password,
    }
    assert generated_password not in config_path.read_text(encoding="utf-8")


@pytest.mark.skip(reason="panel interaction moved to controller-level tests")
def test_panel_rotate_password_reuses_existing_password_env_and_preserves_mappings(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.json"
    secret_path = tmp_path / ".env.secrets"
    original_config = None
    generated_password = "Bb2@cdefghijklmnopqr"

    _write_management_config(
        config_path,
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
    original_config = config_path.read_text(encoding="utf-8")
    secret_path.write_text("ROUTER_ONE_SECRET=old-secret\n", encoding="utf-8")
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    result = runner.invoke(
        app,
        ["panel", "--config", str(config_path)],
        input="\n".join(("1", "2", "1", "y", "", "0")) + "\n",
    )

    assert result.exit_code == 0
    assert config_path.read_text(encoding="utf-8") == original_config
    assert SecretEnvFile(path=secret_path).read() == {
        "ROUTER_ONE_SECRET": generated_password,
    }
    payload = json.loads(config_path.read_text(encoding="utf-8"))
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


@pytest.mark.skip(reason="panel interaction moved to controller-level tests")
def test_panel_rotate_password_switches_password_file_to_env_and_clears_password_file(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.json"
    secret_path = tmp_path / ".env.secrets"
    generated_password = "Cc3#defghijklmnopqrs"

    _write_management_config(
        config_path,
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
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    result = runner.invoke(
        app,
        ["panel", "--config", str(config_path)],
        input="\n".join(("1", "2", "1", "y", "", "0")) + "\n",
    )

    assert result.exit_code == 0
    payload = json.loads(config_path.read_text(encoding="utf-8"))
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
    assert payload["mappings"] == [
        {
            "auto": True,
            "exclusive": True,
            "managed": True,
            "object_group_name": "fqdn-telegram",
            "route_interface": None,
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "router_id": "router-2",
            "service_key": "telegram",
        }
    ]
    assert SecretEnvFile(path=secret_path).read() == {
        "FQDN_UPDATER_ROUTER_ROUTER_2_PASSWORD": generated_password,
    }


@pytest.mark.skip(reason="panel interaction moved to controller-level tests")
def test_panel_add_rejects_deterministic_password_env_collisions_before_secret_write(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.json"
    secret_path = tmp_path / ".env.secrets"
    password_env = password_env_key_for_router_id("home-router")
    generated_password = "Dd4%efghijklmnopqrst"

    _write_management_config(
        config_path,
        routers=[
            {
                "id": "home-router",
                "name": "Home Router",
                "rci_url": "https://home-router.example/rci/",
                "username": "api-user",
                "password_env": password_env,
                "enabled": True,
            }
        ],
    )
    secret_path.write_text(f"{password_env}=old-secret\n", encoding="utf-8")
    original_config = config_path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    def _unexpected_write_value(*args, **kwargs) -> None:
        raise AssertionError("SecretEnvFile.write_value should not be reached on collision")

    monkeypatch.setattr(SecretEnvFile, "write_value", _unexpected_write_value)

    result = runner.invoke(
        app,
        ["panel", "--config", str(config_path)],
        input="\n".join(
            (
                "1",
                "home_router",
                "",
                "https://home-router.example/rci/",
                "",
                "",
                "",
                "",
                "",
            )
        )
        + "\n",
    )

    assert result.exit_code == 1
    assert f"Password env '{password_env}' is already used by router 'home-router'" in (
        result.stderr
    )
    assert config_path.read_text(encoding="utf-8") == original_config
    assert SecretEnvFile(path=secret_path).read() == {password_env: "old-secret"}


@pytest.mark.skip(reason="panel interaction moved to controller-level tests")
def test_panel_rotate_password_rejects_deterministic_password_env_collisions_before_secret_write(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.json"
    secret_path = tmp_path / ".env.secrets"
    password_env = password_env_key_for_router_id("home-router")
    generated_password = "Ee5&fghijklmnopqrstu"

    _write_management_config(
        config_path,
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
    secret_path.write_text(f"{password_env}=old-secret\n", encoding="utf-8")
    original_config = config_path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    def _unexpected_write_value(*args, **kwargs) -> None:
        raise AssertionError("SecretEnvFile.write_value should not be reached on collision")

    monkeypatch.setattr(SecretEnvFile, "write_value", _unexpected_write_value)

    result = runner.invoke(
        app,
        ["panel", "--config", str(config_path)],
        input="\n".join(("1", "2", "1")) + "\n",
    )

    assert result.exit_code == 1
    assert f"Password env '{password_env}' is already used by router 'home_router'" in (
        result.stderr
    )
    assert config_path.read_text(encoding="utf-8") == original_config
    assert SecretEnvFile(path=secret_path).read() == {password_env: "old-secret"}


@pytest.mark.skip(reason="panel interaction moved to controller-level tests")
def test_panel_add_rolls_back_config_when_secret_write_fails(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.json"
    secret_path = tmp_path / ".env.secrets"
    generated_password = "Ff6*ghijklmnopqrstuv"

    _write_management_config(config_path)
    original_config = config_path.read_text(encoding="utf-8")
    secret_path.write_text("UNRELATED_SECRET=old-secret\n", encoding="utf-8")
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )
    monkeypatch.setattr(panel_module.Confirm, "ask", lambda *args, **kwargs: True)

    def _failing_write_value(*args, **kwargs) -> None:
        raise RuntimeError("secret write failed")

    monkeypatch.setattr(SecretEnvFile, "write_value", _failing_write_value)

    result = runner.invoke(
        app,
        ["panel", "--config", str(config_path)],
        input="\n".join(
            (
                "1",
                "home_router",
                "",
                "https://home-router.example/rci/",
                "",
                "",
                "",
                "",
            )
        )
        + "\n",
    )

    assert result.exit_code == 1
    assert "secret write failed" in result.stderr
    assert config_path.read_text(encoding="utf-8") == original_config
    assert generated_password not in config_path.read_text(encoding="utf-8")
    assert SecretEnvFile(path=secret_path).read() == {"UNRELATED_SECRET": "old-secret"}


@pytest.mark.skip(reason="panel interaction moved to controller-level tests")
def test_panel_rotate_password_rolls_back_password_file_reference_when_secret_write_fails(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "config.json"
    secret_path = tmp_path / ".env.secrets"
    generated_password = "Gg7(ghijklmnopqrstuvw"

    _write_management_config(
        config_path,
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
    original_config = config_path.read_text(encoding="utf-8")
    secret_path.write_text("UNRELATED_SECRET=old-secret\n", encoding="utf-8")
    monkeypatch.setattr(
        panel_module.RciPasswordGenerator, "generate", lambda self: generated_password
    )

    def _failing_write_value(*args, **kwargs) -> None:
        raise RuntimeError("secret write failed")

    monkeypatch.setattr(SecretEnvFile, "write_value", _failing_write_value)

    result = runner.invoke(
        app,
        ["panel", "--config", str(config_path)],
        input="\n".join(("1", "2", "1", "y")) + "\n",
    )

    assert result.exit_code == 1
    assert "secret write failed" in result.stderr
    assert config_path.read_text(encoding="utf-8") == original_config
    assert "/run/secrets/home-router" in config_path.read_text(encoding="utf-8")
    assert SecretEnvFile(path=secret_path).read() == {"UNRELATED_SECRET": "old-secret"}


def test_init_creates_scaffold_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"

    result = runner.invoke(app, ["init", "--config", str(config_path)])

    assert result.exit_code == 0
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["routers"] == []
    assert [service["key"] for service in payload["services"]] == [
        "anime",
        "block",
        "block_p2p_streaming",
        "block_vpn_proxy_privacy",
        "block_dev_hosting_security",
        "block_finance_shopping",
        "block_social_creators",
        "block_news_politics",
        "block_other",
        "geoblock",
        "geoblock_ai",
        "geoblock_dev_cloud_saas",
        "geoblock_media_games",
        "geoblock_shopping_travel",
        "geoblock_enterprise_hardware",
        "geoblock_security_networking",
        "geoblock_finance_payments",
        "geoblock_health_reference",
        "geoblock_other",
        "hodca",
        "hodca_dev_cloud_saas",
        "hodca_network_os_tools",
        "hodca_media_games",
        "hodca_ai_education_research",
        "hodca_social_lifestyle",
        "hodca_finance_shopping",
        "hodca_other",
        "news",
        "porn",
        "cloudflare",
        "cloudfront",
        "digitalocean",
        "discord",
        "google_ai",
        "google_meet",
        "google_play",
        "hdrezka",
        "hetzner",
        "meta",
        "ovh",
        "roblox",
        "telegram",
        "tiktok",
        "twitter",
        "youtube",
    ]
    assert payload["services"][-1]["sources"] == [
        {
            "format": "raw_domain_list",
            "url": "https://raw.githubusercontent.com/itdoginfo/allow-domains/refs/heads/main/Services/youtube.lst",
        }
    ]
    assert payload["mappings"] == []


def test_init_refuses_to_overwrite_existing_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")

    result = runner.invoke(app, ["init", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "already exists" in result.stderr


def test_validate_accepts_scaffold_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"

    init_result = runner.invoke(app, ["init", "--config", str(config_path)])
    validate_result = runner.invoke(app, ["config", "validate", "--config", str(config_path)])

    assert init_result.exit_code == 0
    assert validate_result.exit_code == 0
    assert "Config корректен" in validate_result.stdout


def test_validate_rejects_invalid_json(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{not-json", encoding="utf-8")

    result = runner.invoke(app, ["config", "validate", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Invalid JSON" in result.stderr


def test_validate_rejects_invalid_structure(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": "wrong",
                "routers": [],
                "services": [],
                "mappings": [],
                "runtime": {},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["config", "validate", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Config validation failed" in result.stderr


def test_validate_reports_semantic_errors_with_clean_location(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "version": 1,
                "routers": [
                    {
                        "id": "router-1",
                        "name": "Router 1",
                        "rci_url": "https://router-1.example/rci/",
                        "username": "api-user",
                        "password_env": "ROUTER_ONE_SECRET",
                        "enabled": True,
                    },
                    {
                        "id": "router-1",
                        "name": "Router 2",
                        "rci_url": "https://router-2.example/rci/",
                        "username": "api-user",
                        "password_file": "/run/secrets/router-2",
                        "enabled": True,
                    },
                ],
                "services": [],
                "mappings": [],
                "runtime": {},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["config", "validate", "--config", str(config_path)])

    assert result.exit_code == 1
    assert "Config validation failed" in result.stderr
    assert "- config: duplicate router id 'router-1'" in result.stderr
    assert "Value error" not in result.stderr


def test_dry_run_returns_zero_for_no_changes(monkeypatch) -> None:
    config = _config()
    result_payload = _dry_run_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-100.json"),
        plans=(
            _plan(
                to_add=(),
                to_remove=(),
                unchanged=("keep.example",),
                has_changes=False,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.NO_CHANGES,
                unchanged_count=1,
                route_changed=False,
            ),
        ),
        router_status=RouterResultStatus.NO_CHANGES,
    )
    _install_dry_run_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["dry-run", "--config", "config.json"])

    assert result.exit_code == 0
    assert "Dry-run завершён:" in result.stdout
    assert "planned_changes=0" in result.stdout
    assert "status=no_changes" in result.stdout


def test_dry_run_loads_runtime_secret_env_file_before_running(tmp_path, monkeypatch) -> None:
    secret_path = tmp_path / ".env.secrets"
    secret_path.write_text("ROUTER_ONE_SECRET=secret-from-env-file\n", encoding="utf-8")
    config = AppConfig.model_validate(
        {
            "version": 1,
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_SECRET",
                    "enabled": True,
                }
            ],
            "services": [
                {
                    "key": "telegram",
                    "source_urls": ["https://example.com/telegram.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                }
            ],
            "mappings": [],
            "runtime": {"secrets_env_file": str(secret_path)},
        }
    )
    result_payload = _dry_run_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-106.json"),
        plans=(),
        service_results=(),
        router_status=RouterResultStatus.NO_CHANGES,
    )
    monkeypatch.delenv("ROUTER_ONE_SECRET", raising=False)
    monkeypatch.setattr(
        cli_app_module,
        "_validation_service",
        lambda: StubValidationService(config=config),
    )
    monkeypatch.setattr(
        cli_app_module,
        "_dry_run_orchestrator",
        lambda **_kwargs: EnvAssertingOrchestrator(
            result=result_payload,
            key="ROUTER_ONE_SECRET",
            value="secret-from-env-file",
        ),
    )

    result = runner.invoke(app, ["dry-run", "--config", str(tmp_path / "config.json")])

    assert result.exit_code == 0


def test_dry_run_passes_explicit_scheduled_trigger(monkeypatch) -> None:
    config = _config()
    expected_result = _dry_run_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-trigger.json"),
        plans=(),
        service_results=(),
        router_status=RouterResultStatus.NO_CHANGES,
    )
    recorded_triggers: list[RunTrigger] = []

    class _RecordingOrchestrator:
        def run(self, *, config: AppConfig, trigger: RunTrigger) -> DryRunExecutionResult:
            del config
            recorded_triggers.append(trigger)
            return expected_result

    monkeypatch.setattr(
        cli_app_module,
        "_validation_service",
        lambda: StubValidationService(config=config),
    )
    monkeypatch.setattr(
        cli_app_module,
        "_dry_run_orchestrator",
        lambda **_kwargs: _RecordingOrchestrator(),
    )

    result = runner.invoke(
        app,
        ["dry-run", "--config", "config.json", "--trigger", "scheduled"],
    )

    assert result.exit_code == 0
    assert recorded_triggers == [RunTrigger.SCHEDULED]


def test_dry_run_returns_thirty_for_changes_and_json_output(monkeypatch) -> None:
    config = _config()
    result_payload = _dry_run_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-101.json"),
        plans=(
            _plan(
                to_add=("new.example",),
                to_remove=("old.example",),
                unchanged=("keep.example",),
                has_changes=True,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.UPDATED,
                added_count=1,
                removed_count=1,
                unchanged_count=1,
                route_changed=True,
            ),
        ),
        router_status=RouterResultStatus.UPDATED,
    )
    _install_dry_run_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["dry-run", "--config", "config.json", "--output", "json"])

    assert result.exit_code == 30
    payload = json.loads(result.stdout)
    assert payload["artifact_path"] == "data/artifacts/run-101.json"
    assert payload["artifact"]["status"] == "success"
    assert payload["plans"] == [
        {
            "desired_route_binding": {
                "auto": True,
                "exclusive": False,
                "object_group_name": "svc-telegram",
                "route_interface": None,
                "route_target_type": "interface",
                "route_target_value": "Wireguard0",
            },
            "has_changes": True,
            "object_group_diff": {
                "has_changes": True,
                "needs_create": False,
                "object_group_name": "svc-telegram",
                "to_add": ["new.example"],
                "to_remove": ["old.example"],
                "typed_to_add": [{"kind": "domain", "value": "new.example"}],
                "typed_to_remove": [{"kind": "domain", "value": "old.example"}],
                "typed_unchanged": [{"kind": "domain", "value": "keep.example"}],
                "unchanged": ["keep.example"],
            },
            "object_group_name": "svc-telegram",
            "remove_object_group": False,
            "remove_route": False,
            "router_id": "router-1",
            "route_binding_diff": {
                "current_binding": {
                    "auto": True,
                    "exclusive": False,
                    "exists": True,
                    "object_group_name": "svc-telegram",
                    "route_interface": None,
                    "route_target_type": "interface",
                    "route_target_value": "Other0",
                },
                "desired_binding": {
                    "auto": True,
                    "exclusive": False,
                    "object_group_name": "svc-telegram",
                    "route_interface": None,
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                },
                "has_changes": True,
                "object_group_name": "svc-telegram",
            },
            "service_key": "telegram",
        }
    ]


def test_dry_run_returns_twenty_for_partial_result(monkeypatch) -> None:
    config = _config()
    result_payload = _dry_run_result(
        status=RunStatus.PARTIAL,
        artifact_path=Path("data/artifacts/run-102.json"),
        plans=(
            _plan(
                to_add=("new.example",),
                to_remove=(),
                unchanged=(),
                has_changes=True,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.UPDATED,
                added_count=1,
            ),
            ServiceRunResult(
                service_key="youtube",
                object_group_name="svc-youtube",
                status=ServiceResultStatus.FAILED,
                error_message="timeout",
            ),
        ),
        router_status=RouterResultStatus.PARTIAL,
    )
    _install_dry_run_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["dry-run", "--config", "config.json"])

    assert result.exit_code == 20
    assert "status=partial" in result.stdout
    assert "failed_services=1" in result.stdout
    assert "ошибка: timeout" in result.stdout


def test_dry_run_returns_thirty_for_route_only_changes(monkeypatch) -> None:
    config = _config()
    result_payload = _dry_run_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-105.json"),
        plans=(_route_only_plan(),),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.UPDATED,
                unchanged_count=1,
                route_changed=True,
            ),
        ),
        router_status=RouterResultStatus.UPDATED,
    )
    _install_dry_run_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["dry-run", "--config", "config.json"])

    assert result.exit_code == 30
    assert "planned_changes=1" in result.stdout
    assert "route_changed=yes" in result.stdout


def test_dry_run_returns_forty_for_invalid_config(monkeypatch) -> None:
    class FailingValidationService:
        def validate(self, path: Path) -> AppConfig:
            raise RuntimeError(f"Invalid JSON in config file {path}: broken")

    monkeypatch.setattr(cli_app_module, "_validation_service", lambda: FailingValidationService())

    result = runner.invoke(app, ["dry-run", "--config", "missing.json"])

    assert result.exit_code == 40
    assert "Invalid JSON in config file missing.json: broken" in result.stderr


def test_dry_run_returns_fifty_when_run_lock_is_held(monkeypatch) -> None:
    config = _config()

    class _LockingOrchestrator:
        def run(self, *, config: AppConfig, trigger: RunTrigger) -> DryRunExecutionResult:
            del config, trigger
            raise RunLockError(
                "Another run is already in progress (lock file: data/state/run.lock)"
            )

    monkeypatch.setattr(
        cli_app_module,
        "_validation_service",
        lambda: StubValidationService(config=config),
    )
    monkeypatch.setattr(
        cli_app_module,
        "_dry_run_orchestrator",
        lambda **_kwargs: _LockingOrchestrator(),
    )

    result = runner.invoke(app, ["dry-run", "--config", "config.json"])

    assert result.exit_code == 50
    assert "Another run is already in progress" in result.stderr


def test_sync_returns_zero_for_no_changes(monkeypatch) -> None:
    config = _config()
    result_payload = _sync_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-200.json"),
        plans=(
            _plan(
                to_add=(),
                to_remove=(),
                unchanged=("keep.example",),
                has_changes=False,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.NO_CHANGES,
                unchanged_count=1,
                route_changed=False,
            ),
        ),
        router_status=RouterResultStatus.NO_CHANGES,
    )
    _install_sync_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["sync", "--config", "config.json"])

    assert result.exit_code == 0
    assert "Sync завершён:" in result.stdout
    assert "planned_changes=0" in result.stdout
    assert "skipped_services=0" in result.stdout


def test_sync_passes_explicit_openclaw_trigger(monkeypatch) -> None:
    config = _config()
    expected_result = _sync_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-sync-trigger.json"),
        plans=(),
        service_results=(),
        router_status=RouterResultStatus.NO_CHANGES,
    )
    recorded_triggers: list[RunTrigger] = []

    class _RecordingOrchestrator:
        def run(self, *, config: AppConfig, trigger: RunTrigger) -> SyncExecutionResult:
            del config
            recorded_triggers.append(trigger)
            return expected_result

    monkeypatch.setattr(
        cli_app_module,
        "_validation_service",
        lambda: StubValidationService(config=config),
    )
    monkeypatch.setattr(
        cli_app_module,
        "_sync_orchestrator",
        lambda **_kwargs: _RecordingOrchestrator(),
    )

    result = runner.invoke(
        app,
        ["sync", "--config", "config.json", "--trigger", "openclaw"],
    )

    assert result.exit_code == 0
    assert recorded_triggers == [RunTrigger.OPENCLAW]


def test_sync_returns_ten_for_applied_changes_and_json_output(monkeypatch) -> None:
    config = _config()
    result_payload = _sync_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-201.json"),
        plans=(
            _plan(
                to_add=("new.example",),
                to_remove=("old.example",),
                unchanged=("keep.example",),
                has_changes=True,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.UPDATED,
                added_count=1,
                removed_count=1,
                unchanged_count=1,
                route_changed=True,
            ),
        ),
        router_status=RouterResultStatus.UPDATED,
    )
    _install_sync_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["sync", "--config", "config.json", "--output", "json"])

    assert result.exit_code == 10
    payload = json.loads(result.stdout)
    assert payload["artifact_path"] == "data/artifacts/run-201.json"
    assert payload["artifact"]["mode"] == "apply"
    assert payload["artifact"]["status"] == "success"
    assert payload["plans"][0]["object_group_diff"]["has_changes"] is True
    assert payload["plans"][0]["route_binding_diff"]["has_changes"] is True


def test_sync_returns_twenty_for_partial_result(monkeypatch) -> None:
    config = _config()
    result_payload = _sync_result(
        status=RunStatus.PARTIAL,
        artifact_path=Path("data/artifacts/run-202.json"),
        plans=(
            _plan(
                to_add=("new.example",),
                to_remove=(),
                unchanged=(),
                has_changes=True,
            ),
        ),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.FAILED,
                error_message="Write stage failed for service 'telegram': timeout",
            ),
            ServiceRunResult(
                service_key="youtube",
                object_group_name="svc-youtube",
                status=ServiceResultStatus.SKIPPED,
                error_message="Skipped after router write failure: timeout",
            ),
        ),
        router_status=RouterResultStatus.PARTIAL,
    )
    _install_sync_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["sync", "--config", "config.json"])

    assert result.exit_code == 20
    assert "status=partial" in result.stdout
    assert "skipped_services=1" in result.stdout
    assert "ошибка: Skipped after router write failure: timeout" in result.stdout


def test_sync_returns_ten_for_route_only_changes(monkeypatch) -> None:
    config = _config()
    result_payload = _sync_result(
        status=RunStatus.SUCCESS,
        artifact_path=Path("data/artifacts/run-203.json"),
        plans=(_route_only_plan(),),
        service_results=(
            ServiceRunResult(
                service_key="telegram",
                object_group_name="svc-telegram",
                status=ServiceResultStatus.UPDATED,
                unchanged_count=1,
                route_changed=True,
            ),
        ),
        router_status=RouterResultStatus.UPDATED,
    )
    _install_sync_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["sync", "--config", "config.json"])

    assert result.exit_code == 10
    assert "planned_changes=1" in result.stdout
    assert "route_changed=yes" in result.stdout


def test_sync_returns_forty_for_invalid_config(monkeypatch) -> None:
    class FailingValidationService:
        def validate(self, path: Path) -> AppConfig:
            raise RuntimeError(f"Config validation failed for {path}")

    monkeypatch.setattr(cli_app_module, "_validation_service", lambda: FailingValidationService())

    result = runner.invoke(app, ["sync", "--config", "missing.json"])

    assert result.exit_code == 40
    assert "Config validation failed for missing.json" in result.stderr


def test_sync_returns_fifty_when_run_lock_is_held(monkeypatch) -> None:
    config = _config()

    class _LockingOrchestrator:
        def run(self, *, config: AppConfig, trigger: RunTrigger) -> SyncExecutionResult:
            del config, trigger
            raise RunLockError(
                "Another run is already in progress (lock file: data/state/run.lock)"
            )

    monkeypatch.setattr(
        cli_app_module,
        "_validation_service",
        lambda: StubValidationService(config=config),
    )
    monkeypatch.setattr(
        cli_app_module,
        "_sync_orchestrator",
        lambda **_kwargs: _LockingOrchestrator(),
    )

    result = runner.invoke(app, ["sync", "--config", "config.json"])

    assert result.exit_code == 50
    assert "Another run is already in progress" in result.stderr


def test_status_returns_zero_for_healthy_result(monkeypatch) -> None:
    config = _config()
    result_payload = _status_result(
        overall_status=OverallDiagnosticStatus.HEALTHY,
        router_results=(
            RouterStatusDiagnostic(
                router_id="router-1",
                status=RouterDiagnosticStatus.HEALTHY,
                dns_proxy_enabled=True,
            ),
        ),
    )
    _install_status_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["status", "--config", "config.json"])

    assert result.exit_code == 0
    assert "Status завершён:" in result.stdout
    assert "overall_status=healthy" in result.stdout
    assert "dns_proxy=enabled" in result.stdout


def test_status_returns_twenty_for_degraded_result_and_json_output(monkeypatch) -> None:
    config = _config()
    result_payload = _status_result(
        overall_status=OverallDiagnosticStatus.DEGRADED,
        router_results=(
            RouterStatusDiagnostic(
                router_id="router-1",
                status=RouterDiagnosticStatus.DEGRADED,
                dns_proxy_enabled=False,
            ),
        ),
    )
    _install_status_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["status", "--config", "config.json", "--output", "json"])

    assert result.exit_code == 20
    payload = json.loads(result.stdout)
    assert payload["config_ready"] is True
    assert payload["overall_status"] == "degraded"
    assert payload["checked_router_count"] == 1
    assert payload["router_results"][0]["dns_proxy_enabled"] is False


def test_status_returns_twenty_for_failed_result(monkeypatch) -> None:
    config = _config()
    result_payload = _status_result(
        overall_status=OverallDiagnosticStatus.FAILED,
        router_results=(
            RouterStatusDiagnostic(
                router_id="router-1",
                status=RouterDiagnosticStatus.FAILED,
                dns_proxy_enabled=None,
                error_message="authentication failed",
                failure_step=RunStep.CLIENT_CREATE,
            ),
        ),
    )
    _install_status_stubs(monkeypatch, config=config, result=result_payload)

    result = runner.invoke(app, ["status", "--config", "config.json"])

    assert result.exit_code == 20
    assert "status=failed" in result.stdout
    assert "failure_step: client_create" in result.stdout
    assert "ошибка: authentication failed" in result.stdout


def test_status_returns_forty_for_invalid_config(monkeypatch) -> None:
    class FailingValidationService:
        def validate(self, path: Path) -> AppConfig:
            raise RuntimeError(f"Config validation failed for {path}")

    monkeypatch.setattr(cli_app_module, "_validation_service", lambda: FailingValidationService())

    result = runner.invoke(app, ["status", "--config", "missing.json"])

    assert result.exit_code == 40
    assert "Config validation failed for missing.json" in result.stderr


def test_dry_run_never_calls_write_methods(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(_config().model_dump(mode="json")), encoding="utf-8")
    monkeypatch.setenv("ROUTER_ONE_SECRET", "secret-1")

    artifact_writer = RecordingArtifactWriter()
    client_factory = RecordingClientFactory(
        states={
            ("router-1", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=("keep.example",),
                exists=True,
            )
        },
        route_bindings={
            ("router-1", "svc-telegram"): RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Wireguard0",
                auto=True,
                exclusive=False,
            )
        },
    )
    orchestrator = DryRunOrchestrator(
        source_loader=StubSourceLoader(
            SourceLoadReport(
                loaded=(
                    NormalizedServiceSource(
                        service_key="telegram",
                        entries=("keep.example", "new.example"),
                    ),
                )
            )
        ),
        secret_resolver=cli_app_module.EnvironmentFileSecretResolver(),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=artifact_writer,
        now_provider=lambda: datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
        run_id_factory=lambda: "run-103",
    )
    monkeypatch.setattr(cli_app_module, "_dry_run_orchestrator", lambda **_kwargs: orchestrator)

    result = runner.invoke(app, ["dry-run", "--config", str(config_path)])

    assert result.exit_code == 30
    assert artifact_writer.last_artifact is not None
    assert client_factory.clients["router-1"].read_calls == [
        "svc-telegram",
        "svc-telegram-2",
        "svc-telegram-3",
        "svc-telegram-4",
        "route:svc-telegram",
        "route:svc-telegram-2",
        "route:svc-telegram-3",
        "route:svc-telegram-4",
    ]
    assert client_factory.clients["router-1"].write_calls == []


class StubValidationService:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def validate(self, path: Path) -> AppConfig:
        return self._config


class StubOrchestrator:
    def __init__(self, result: DryRunExecutionResult) -> None:
        self._result = result

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> DryRunExecutionResult:
        assert trigger is RunTrigger.MANUAL
        return self._result


class EnvAssertingOrchestrator:
    def __init__(self, *, result: DryRunExecutionResult, key: str, value: str) -> None:
        self._result = result
        self._key = key
        self._value = value

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> DryRunExecutionResult:
        assert trigger is RunTrigger.MANUAL
        assert os.environ[self._key] == self._value
        return self._result


class StubSyncOrchestrator:
    def __init__(self, result: SyncExecutionResult) -> None:
        self._result = result

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> SyncExecutionResult:
        assert trigger is RunTrigger.MANUAL
        return self._result


class StubStatusService:
    def __init__(self, result: StatusDiagnosticsResult) -> None:
        self._result = result

    def check(self, *, config: AppConfig) -> StatusDiagnosticsResult:
        return self._result


class StubSourceLoader:
    def __init__(self, report: SourceLoadReport) -> None:
        self._report = report

    def load_enabled_services(self, services: object) -> SourceLoadReport:
        return self._report


class RecordingArtifactWriter:
    def __init__(self) -> None:
        self.last_artifact: RunArtifact | None = None

    def write(self, config: AppConfig, artifact: RunArtifact) -> Path:
        self.last_artifact = artifact
        return Path(config.runtime.artifacts_dir) / f"{artifact.run_id}.json"


class RecordingClient(KeeneticClient):
    def __init__(
        self,
        states: dict[str, ObjectGroupState],
        route_bindings: dict[str, RouteBindingState],
        static_routes: tuple[StaticRouteState, ...] = (),
    ) -> None:
        self._states = states
        self._route_bindings = route_bindings
        self._static_routes = static_routes
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []

    def get_object_group(self, name: str) -> ObjectGroupState:
        self.read_calls.append(name)
        return self._states.get(name, ObjectGroupState(name=name, entries=(), exists=False))

    def get_route_binding(self, object_group_name: str) -> RouteBindingState:
        self.read_calls.append(f"route:{object_group_name}")
        return self._route_bindings.get(
            object_group_name,
            RouteBindingState(object_group_name=object_group_name, exists=False),
        )

    def get_static_routes(self) -> tuple[StaticRouteState, ...]:
        self.read_calls.append("static_routes")
        return self._static_routes

    def ensure_object_group(self, name: str) -> None:
        self.write_calls.append(f"ensure_object_group:{name}")

    def remove_object_group(self, name: str) -> None:
        self.write_calls.append(f"remove_object_group:{name}")

    def add_entries(self, name: str, items: object) -> None:
        self.write_calls.append(f"add_entries:{name}")

    def remove_entries(self, name: str, items: object) -> None:
        self.write_calls.append(f"remove_entries:{name}")

    def ensure_route(self, binding: RouteBindingSpec) -> None:
        self.write_calls.append(f"ensure_route:{binding.object_group_name}")

    def remove_route(self, binding: RouteBindingState) -> None:
        self.write_calls.append(f"remove_route:{binding.object_group_name}")

    def ensure_static_route(self, route: StaticRouteSpec) -> None:
        self.write_calls.append(f"ensure_static_route:{route.network}")

    def remove_static_route(self, route: StaticRouteState) -> None:
        self.write_calls.append(f"remove_static_route:{route.network}")

    def save_config(self) -> None:
        self.write_calls.append("save_config")

    def get_dns_proxy_status(self) -> DnsProxyStatus:
        return DnsProxyStatus(enabled=True)


class RecordingClientFactory(KeeneticClientFactory):
    def __init__(
        self,
        states: dict[tuple[str, str], ObjectGroupState],
        route_bindings: dict[tuple[str, str], RouteBindingState],
        static_routes: dict[str, tuple[StaticRouteState, ...]] | None = None,
    ) -> None:
        self._states = states
        self._route_bindings = route_bindings
        self._static_routes = static_routes or {}
        self.clients: dict[str, RecordingClient] = {}

    def create(self, router: RouterConfig, password: str) -> KeeneticClient:
        router_states = {
            group_name: state
            for (router_id, group_name), state in self._states.items()
            if router_id == router.id
        }
        router_route_bindings = {
            group_name: state
            for (router_id, group_name), state in self._route_bindings.items()
            if router_id == router.id
        }
        client = RecordingClient(
            states=router_states,
            route_bindings=router_route_bindings,
            static_routes=self._static_routes.get(router.id, ()),
        )
        self.clients[router.id] = client
        return client


def _install_dry_run_stubs(
    monkeypatch,
    *,
    config: AppConfig,
    result: DryRunExecutionResult,
) -> None:
    monkeypatch.setattr(
        cli_app_module,
        "_validation_service",
        lambda: StubValidationService(config=config),
    )
    monkeypatch.setattr(
        cli_app_module,
        "_dry_run_orchestrator",
        lambda **_kwargs: StubOrchestrator(result=result),
    )


def _install_sync_stubs(
    monkeypatch,
    *,
    config: AppConfig,
    result: SyncExecutionResult,
) -> None:
    monkeypatch.setattr(
        cli_app_module,
        "_validation_service",
        lambda: StubValidationService(config=config),
    )
    monkeypatch.setattr(
        cli_app_module,
        "_sync_orchestrator",
        lambda **_kwargs: StubSyncOrchestrator(result=result),
    )


def _install_status_stubs(
    monkeypatch,
    *,
    config: AppConfig,
    result: StatusDiagnosticsResult,
) -> None:
    monkeypatch.setattr(
        cli_app_module,
        "_validation_service",
        lambda: StubValidationService(config=config),
    )
    monkeypatch.setattr(
        cli_app_module,
        "_status_service",
        lambda: StubStatusService(result=result),
    )


def _dry_run_result(
    *,
    status: RunStatus,
    artifact_path: Path,
    plans: tuple[ServiceSyncPlan, ...],
    service_results: tuple[ServiceRunResult, ...],
    router_status: RouterResultStatus,
) -> DryRunExecutionResult:
    return DryRunExecutionResult(
        artifact=RunArtifact(
            run_id=artifact_path.stem,
            trigger=RunTrigger.MANUAL,
            mode=RunMode.DRY_RUN,
            status=status,
            started_at=datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            log_path=Path("data/logs") / f"{artifact_path.stem}.log",
            router_results=[
                RouterRunResult(
                    router_id="router-1",
                    status=router_status,
                    service_results=list(service_results),
                )
            ],
        ),
        artifact_path=artifact_path,
        plans=plans,
    )


def _sync_result(
    *,
    status: RunStatus,
    artifact_path: Path,
    plans: tuple[ServiceSyncPlan, ...],
    service_results: tuple[ServiceRunResult, ...],
    router_status: RouterResultStatus,
) -> SyncExecutionResult:
    return SyncExecutionResult(
        artifact=RunArtifact(
            run_id=artifact_path.stem,
            trigger=RunTrigger.MANUAL,
            mode=RunMode.APPLY,
            status=status,
            started_at=datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            log_path=Path("data/logs") / f"{artifact_path.stem}.log",
            router_results=[
                RouterRunResult(
                    router_id="router-1",
                    status=router_status,
                    service_results=list(service_results),
                )
            ],
        ),
        artifact_path=artifact_path,
        plans=plans,
    )


def _status_result(
    *,
    overall_status: OverallDiagnosticStatus,
    router_results: tuple[RouterStatusDiagnostic, ...],
) -> StatusDiagnosticsResult:
    return StatusDiagnosticsResult(
        config_ready=True,
        overall_status=overall_status,
        checked_router_count=len(router_results),
        router_results=router_results,
    )


def _plan(
    *,
    to_add: tuple[str, ...],
    to_remove: tuple[str, ...],
    unchanged: tuple[str, ...],
    has_changes: bool,
) -> ServiceSyncPlan:
    desired_route_binding = RouteBindingSpec(
        object_group_name="svc-telegram",
        route_target_type="interface",
        route_target_value="Wireguard0",
        route_interface=None,
        auto=True,
        exclusive=False,
    )
    current_route_value = "Other0" if has_changes else "Wireguard0"
    return ServiceSyncPlan(
        service_key="telegram",
        router_id="router-1",
        object_group_name="svc-telegram",
        object_group_diff=ObjectGroupDiff(
            object_group_name="svc-telegram",
            needs_create=False,
            to_add=to_add,
            to_remove=to_remove,
            unchanged=unchanged,
            has_changes=has_changes,
        ),
        desired_route_binding=desired_route_binding,
        route_binding_diff=RouteBindingDiff(
            object_group_name="svc-telegram",
            current_binding=RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value=current_route_value,
                auto=True,
                exclusive=False,
            ),
            desired_binding=desired_route_binding,
            has_changes=has_changes,
        ),
    )


def _route_only_plan() -> ServiceSyncPlan:
    desired_route_binding = RouteBindingSpec(
        object_group_name="svc-telegram",
        route_target_type="interface",
        route_target_value="Wireguard0",
        route_interface=None,
        auto=True,
        exclusive=False,
    )
    return ServiceSyncPlan(
        service_key="telegram",
        router_id="router-1",
        object_group_name="svc-telegram",
        object_group_diff=ObjectGroupDiff(
            object_group_name="svc-telegram",
            needs_create=False,
            to_add=(),
            to_remove=(),
            unchanged=("keep.example",),
            has_changes=False,
        ),
        desired_route_binding=desired_route_binding,
        route_binding_diff=RouteBindingDiff(
            object_group_name="svc-telegram",
            current_binding=RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Other0",
                auto=False,
                exclusive=False,
            ),
            desired_binding=desired_route_binding,
            has_changes=True,
        ),
    )


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "version": 1,
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_SECRET",
                    "enabled": True,
                }
            ],
            "services": [
                {
                    "key": "telegram",
                    "source_urls": ["https://example.com/telegram.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                }
            ],
            "mappings": [
                {
                    "router_id": "router-1",
                    "service_key": "telegram",
                    "object_group_name": "svc-telegram",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                }
            ],
            "runtime": {"artifacts_dir": "data/artifacts", "logs_dir": "data/logs"},
        }
    )


def _write_management_config(
    path: Path,
    *,
    routers: list[dict[str, object]] | None = None,
    mappings: list[dict[str, object]] | None = None,
) -> None:
    config = AppConfig.model_validate(
        {
            "routers": routers or [],
            "services": [
                {
                    "key": "telegram",
                    "source_urls": ["https://example.com/telegram.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
                {
                    "key": "youtube",
                    "source_urls": ["https://example.com/youtube.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
            ],
            "mappings": mappings or [],
        }
    )
    path.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
