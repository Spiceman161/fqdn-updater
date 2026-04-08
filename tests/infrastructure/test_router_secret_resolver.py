from __future__ import annotations

import pytest

from fqdn_updater.domain.config_schema import RouterConfig
from fqdn_updater.infrastructure.router_secret_resolver import EnvironmentFileSecretResolver


def test_resolve_reads_password_from_env(monkeypatch) -> None:
    resolver = EnvironmentFileSecretResolver()
    router = _router_config(password_env="ROUTER_ONE_PASSWORD")
    monkeypatch.setenv("ROUTER_ONE_PASSWORD", "  secret-from-env  ")

    assert resolver.resolve(router) == "secret-from-env"


def test_resolve_reads_password_from_file(tmp_path) -> None:
    resolver = EnvironmentFileSecretResolver()
    secret_path = tmp_path / "router.secret"
    secret_path.write_text("secret-from-file\n", encoding="utf-8")
    router = _router_config(password_file=str(secret_path))

    assert resolver.resolve(router) == "secret-from-file"


def test_resolve_rejects_missing_env(monkeypatch) -> None:
    resolver = EnvironmentFileSecretResolver()
    router = _router_config(password_env="ROUTER_ONE_PASSWORD")
    monkeypatch.delenv("ROUTER_ONE_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="is not set"):
        resolver.resolve(router)


def test_resolve_rejects_missing_file() -> None:
    resolver = EnvironmentFileSecretResolver()
    router = _router_config(password_file="/tmp/does-not-exist.secret")

    with pytest.raises(RuntimeError, match="could not be read"):
        resolver.resolve(router)


def test_resolve_rejects_blank_secret_from_file(tmp_path) -> None:
    resolver = EnvironmentFileSecretResolver()
    secret_path = tmp_path / "router.secret"
    secret_path.write_text(" \n", encoding="utf-8")
    router = _router_config(password_file=str(secret_path))

    with pytest.raises(RuntimeError, match="is blank"):
        resolver.resolve(router)


def _router_config(
    *,
    password_env: str | None = None,
    password_file: str | None = None,
) -> RouterConfig:
    payload = {
        "id": "router-1",
        "name": "Router 1",
        "rci_url": "https://router-1.example/rci/",
        "username": "api-user",
        "enabled": True,
    }
    if password_env is not None:
        payload["password_env"] = password_env
    if password_file is not None:
        payload["password_file"] = password_file
    return RouterConfig.model_validate(payload)
