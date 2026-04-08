from __future__ import annotations

import json

from typer.testing import CliRunner

from fqdn_updater.cli.app import app

runner = CliRunner()


def test_root_help_shows_expected_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "init" in result.stdout
    assert "config" in result.stdout


def test_init_creates_scaffold_config(tmp_path) -> None:
    config_path = tmp_path / "config.json"

    result = runner.invoke(app, ["init", "--config", str(config_path)])

    assert result.exit_code == 0
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["routers"] == []
    assert payload["services"] == []
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
    assert "Config is valid" in validate_result.stdout


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
