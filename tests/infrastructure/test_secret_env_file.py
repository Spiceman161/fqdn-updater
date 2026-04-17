from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from fqdn_updater.infrastructure.secret_env_file import (
    SecretEnvFile,
    password_env_key_for_router_id,
)


def test_password_env_key_for_router_id_is_deterministic() -> None:
    assert (
        password_env_key_for_router_id("home-router_1")
        == "FQDN_UPDATER_ROUTER_HOME_ROUTER_1_PASSWORD"
    )


def test_secret_env_file_reads_values_and_loads_without_overriding_process_env(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / ".env.secrets"
    path.write_text(
        "\n".join(
            (
                "# local secrets",
                "ROUTER_ONE=from-file",
                "export ROUTER_TWO='quoted-value'",
                "",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ROUTER_ONE", "from-process")
    monkeypatch.delenv("ROUTER_TWO", raising=False)

    env_file = SecretEnvFile(path=path)

    assert env_file.read() == {
        "ROUTER_ONE": "from-file",
        "ROUTER_TWO": "quoted-value",
    }
    env_file.load_into_environment()
    assert env_file.read()["ROUTER_ONE"] == "from-file"
    assert os.environ["ROUTER_ONE"] == "from-process"
    assert os.environ["ROUTER_TWO"] == "quoted-value"


def test_secret_env_file_write_value_preserves_existing_keys(tmp_path) -> None:
    path = tmp_path / ".env.secrets"
    path.write_text("EXISTING=secret\n", encoding="utf-8")

    SecretEnvFile(path=path).write_value(key="NEW_SECRET", value="generated-secret")

    assert SecretEnvFile(path=path).read() == {
        "EXISTING": "secret",
        "NEW_SECRET": "generated-secret",
    }


def test_secret_env_file_round_trips_special_characters(tmp_path) -> None:
    path = tmp_path / ".env.secrets"
    value = "#!$\"'\\<>"

    SecretEnvFile(path=path).write_value(key="SPECIAL_SECRET", value=value)

    assert SecretEnvFile(path=path).read() == {"SPECIAL_SECRET": value}


def test_secret_env_file_write_value_rejects_blank_values(tmp_path) -> None:
    path = tmp_path / ".env.secrets"

    with pytest.raises(RuntimeError, match="must not be blank"):
        SecretEnvFile(path=path).write_value(key="BLANK_SECRET", value="   ")


def test_secret_env_file_write_value_is_atomic_on_replace_failure(
    monkeypatch,
    tmp_path,
) -> None:
    path = tmp_path / ".env.secrets"
    path.write_text("EXISTING=secret\n", encoding="utf-8")
    original_content = path.read_text(encoding="utf-8")
    original_replace = Path.replace

    def failing_replace(self: Path, target: Path) -> Path:
        if self.name.startswith(f".{path.name}.") and self.suffix == ".tmp":
            raise OSError("simulated replace failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)

    with pytest.raises(RuntimeError, match="could not be written"):
        SecretEnvFile(path=path).write_value(key="NEW_SECRET", value="generated-secret")

    assert path.read_text(encoding="utf-8") == original_content


def test_secret_env_file_write_value_sets_owner_only_permissions(tmp_path) -> None:
    path = tmp_path / ".env.secrets"

    SecretEnvFile(path=path).write_value(key="NEW_SECRET", value="generated-secret")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_secret_env_file_rejects_invalid_lines(tmp_path) -> None:
    path = tmp_path / ".env.secrets"
    path.write_text("not-an-env-line\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="must use KEY=value"):
        SecretEnvFile(path=path).read()
