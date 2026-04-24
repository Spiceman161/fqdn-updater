from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = ROOT / "install.sh"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_packaging_assets_exist() -> None:
    for relative_path in [
        "Dockerfile",
        ".dockerignore",
        "compose.yaml",
        "CONTRIBUTING.md",
        "SECURITY.md",
        ".github/dependabot.yml",
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
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
        "ExecStart=/usr/bin/docker compose run --rm fqdn-updater "
        "sync --trigger scheduled --config /work/config.json" in service
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
        "schedule install",
    ]:
        assert text in quickstart, text

    assert "Docker Compose runtime" in readme
    assert "systemd timer" in readme
    assert "schedule install" in readme
    assert (
        "curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh"
        in readme
    )


def test_install_script_exists_and_passes_bash_syntax_check() -> None:
    assert INSTALL_SH.is_file()

    subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        check=True,
        cwd=ROOT,
    )


def test_install_script_covers_expected_installation_contract() -> None:
    install_script = _read("install.sh")

    for text in [
        'readonly REPOSITORY_OWNER="Spiceman161"',
        'readonly REPOSITORY_NAME="fqdn-updater"',
        'readonly REPOSITORY_SLUG="${REPOSITORY_OWNER}/${REPOSITORY_NAME}"',
        "/opt/fqdn-updater",
        "/usr/local/bin/fqdn-updater",
        "domaingo",
        "docker-compose-plugin",
        "systemctl enable --now docker",
        '"${GITHUB_API_URL}/releases/latest"',
        'git ls-remote --tags --refs "https://github.com/${REPOSITORY_SLUG}.git"',
        '"${VENV_DIR}/bin/fqdn-updater" init --config "${CONFIG_PATH}"',
        '"${VENV_DIR}/bin/fqdn-updater" schedule set-daily \\',
        '"${VENV_DIR}/bin/fqdn-updater" schedule install --config "${CONFIG_PATH}"',
        "stage_preserved_paths",
        "restore_preserved_paths",
        'find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +',
    ]:
        assert text in install_script, text


def test_install_script_uses_clean_deploy_while_preserving_operator_state() -> None:
    install_script = _read("install.sh")

    deploy_start = install_script.index("deploy_release()")
    deploy_end = install_script.index("install_virtualenv()")
    deploy_block = install_script[deploy_start:deploy_end]

    for text in [
        "prepare_install_root",
        'mktemp -d "${INSTALL_DIR}.preserve.XXXXXX"',
        "stage_preserved_paths",
        "clean_install_root",
        "remove_preserved_paths_from_release",
        'cp -a "${release_dir}/." "${INSTALL_DIR}/"',
        "restore_preserved_paths",
        'rmdir "${preserve_dir}"',
    ]:
        assert text in deploy_block, text

    assert deploy_block.index("stage_preserved_paths") < deploy_block.index("clean_install_root")
    assert deploy_block.index("clean_install_root") < deploy_block.index("cp -a")
    assert deploy_block.index("cp -a") < deploy_block.index("restore_preserved_paths")

    for text in [
        '"${CONFIG_PATH}"',
        '"${INSTALL_DIR}"/.env*',
        "data secrets .venv",
    ]:
        assert text in install_script, text


def test_install_script_wrapper_routes_and_security_constraints() -> None:
    install_script = _read("install.sh")

    for text in [
        "sync|dry-run|status)",
        'exec docker compose run --rm fqdn-updater "${command_name}" "$@"',
        "panel|init|config|router|mapping|schedule)",
        'exec "${VENV_CLI}" "${command_name}" "$@"',
    ]:
        assert text in install_script, text

    for marker in [
        "ghp_",
        "github_pat_",
        "glpat-",
        "xoxb-",
        "Authorization: Bearer",
        "token=",
        "TOKEN=",
        ".bashrc",
        ".zshrc",
        ".profile",
        "bash_profile",
    ]:
        assert marker not in install_script, marker
