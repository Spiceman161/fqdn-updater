from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_packaging_assets_exist() -> None:
    for relative_path in [
        "Dockerfile",
        ".dockerignore",
        "compose.yaml",
        "examples/fqdn-updater.service",
        "examples/fqdn-updater.timer",
    ]:
        assert (ROOT / relative_path).is_file(), relative_path


def test_dockerfile_and_compose_contracts_are_static_and_minimal() -> None:
    dockerfile = _read("Dockerfile")
    compose = _read("compose.yaml")

    assert "FROM python:3.12-slim" in dockerfile
    assert "RUN pip install --no-cache-dir ." in dockerfile
    assert 'ENTRYPOINT ["fqdn-updater"]' in dockerfile
    assert 'CMD ["sync", "--config", "/work/config.json"]' in dockerfile

    assert "services:" in compose
    assert "fqdn-updater:" in compose
    assert "build:" in compose
    assert "context: ." in compose
    assert "source: ./config.json" in compose
    assert "target: /work/config.json" in compose
    assert "create_host_path: false" in compose
    assert "./data:/work/data" in compose
    assert 'command: ["sync", "--config", "/work/config.json"]' in compose


def test_systemd_examples_are_one_shot_and_scheduled() -> None:
    service = _read("examples/fqdn-updater.service")
    timer = _read("examples/fqdn-updater.timer")

    assert "Type=oneshot" in service
    assert "docker compose run --rm fqdn-updater" in service
    assert (
        "ExecStart=/usr/bin/docker compose run --rm fqdn-updater sync --config /work/config.json"
        in service
    )
    assert "Persistent=true" in timer
    assert "Unit=fqdn-updater.service" in timer


def test_operator_docs_cover_docker_compose_systemd_and_runtime_paths() -> None:
    quickstart = _read("docs/USER_QUICKSTART.md")
    readme = _read("README.md")

    for text in [
        "Docker Compose runtime",
        "systemd timer",
        "config.json",
        ".env",
        "secrets/",
        "artifacts",
        "logs",
    ]:
        assert text in quickstart, text

    assert "Docker Compose runtime" in readme
    assert "systemd unit/timer" in readme
