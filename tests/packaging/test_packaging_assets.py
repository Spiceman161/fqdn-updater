from __future__ import annotations

import importlib
import subprocess
from importlib import metadata
from pathlib import Path

from fqdn_updater import __version__

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
        "docs/README.md",
        "docs/LLM_CONTEXT.md",
        ".github/dependabot.yml",
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        "examples/fqdn-updater.service",
        "examples/fqdn-updater.timer",
    ]:
        assert (ROOT / relative_path).is_file(), relative_path


def test_source_registry_data_is_packaged_as_python_module() -> None:
    module = importlib.import_module("fqdn_updater.domain.source_registry_data")

    assert module.__name__ == "fqdn_updater.domain.source_registry_data"


def test_package_metadata_version_matches_runtime_version() -> None:
    assert metadata.version("fqdn-updater") == __version__


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
    assert "path: ./.env" in compose
    assert "source: ./config.json" in compose
    assert "target: /work/config.json" in compose
    assert "create_host_path: false" in compose
    assert "source: ./.env.secrets" in compose
    assert "target: /work/.env.secrets" in compose
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
    docs_index = _read("docs/README.md")
    llm_context = _read("docs/LLM_CONTEXT.md")

    for text in [
        "Docker Compose runtime",
        "systemd timer",
        "config.json",
        ".env",
        "secrets/",
        "artifacts",
        "logs",
        "schedule install",
        "static routes",
        "fqdn-updater:<service>",
    ]:
        assert text in quickstart, text

    assert "Docker Compose runtime" in readme
    assert "systemd timer" in readme
    assert "docs/README.md" in readme
    assert "docs/LLM_CONTEXT.md" in readme
    assert "schedule install" in readme
    assert (
        "curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/v1.0.4/install.sh"
        in readme
    )

    for text in [
        "Быстрый старт оператора",
        "Конфигурация",
        "CLI reference",
        "LLM_CONTEXT.md",
        "REFACTORING_PLAN.md",
        "Исторический",
    ]:
        assert text in docs_index, text

    for text in [
        "Production transport только один",
        "cli/",
        "application/",
        "domain/",
        "infrastructure/",
        "raw_domain_list",
        "raw_cidr_list",
        "fqdn-updater:<service>",
        "./scripts/verify.sh",
    ]:
        assert text in llm_context, text


def test_operator_docs_cover_checksum_release_asset_contract() -> None:
    docs = {
        "README.md": _read("README.md"),
        "README_EN.md": _read("README_EN.md"),
        "docs/DEPLOYMENT.md": _read("docs/DEPLOYMENT.md"),
        "docs/USER_QUICKSTART.md": _read("docs/USER_QUICKSTART.md"),
        "docs/LLM_CONTEXT.md": _read("docs/LLM_CONTEXT.md"),
        "SECURITY.md": _read("SECURITY.md"),
    }

    for relative_path, text in docs.items():
        assert "fqdn-updater-<tag>.tar.gz" in text, relative_path
        assert "fqdn-updater-<tag>.tar.gz.sha256" in text, relative_path

    assert "gh release upload" in docs["docs/DEPLOYMENT.md"]
    assert "не заменяет подписи релиза" in docs["docs/DEPLOYMENT.md"]
    assert "компрометации GitHub account" in docs["docs/DEPLOYMENT.md"]
    assert "signature scheme" in docs["SECURITY.md"]
    assert "compromised GitHub account" in docs["SECURITY.md"]


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
        'DOWNLOAD_RELEASE_DIR=""',
        "/opt/fqdn-updater",
        "/usr/local/bin/fqdn-updater",
        "domaingo",
        "docker-compose-plugin",
        "systemctl enable --now docker",
        "docker_runtime_available",
        "require_ubuntu_22_or_later",
        "This installer supports Ubuntu 22.04 and later only.",
        "--version requires a non-empty tag value.",
        "${VERSION_CODENAME} stable",
        'RESOLVED_RELEASE_TAG=""',
        'RELEASE_TARBALL_URL=""',
        'RELEASE_CHECKSUM_URL=""',
        '"${GITHUB_API_URL}/releases/latest"',
        "Cannot resolve latest GitHub Release for ${REPOSITORY_SLUG}.",
        '"${GITHUB_API_URL}/releases/tags/${RELEASE_VERSION}"',
        "Cannot resolve GitHub Release for tag ${RELEASE_VERSION}.",
        "Cannot parse GitHub Release asset metadata.",
        'tarball_name = f"fqdn-updater-{tag_name}.tar.gz"',
        'checksum_name = f"{tarball_name}.sha256"',
        "verify_release_checksum",
        "sha256sum --check --status",
        "Downloaded archive does not contain pyproject.toml.",
        "set_config_permissions",
        'install -m 0600 /dev/null "${INSTALL_DIR}/.env.secrets"',
        'chmod 0644 "${CONFIG_PATH}"',
        '"${VENV_DIR}/bin/fqdn-updater" init --config "${CONFIG_PATH}"',
        '"${VENV_DIR}/bin/fqdn-updater" schedule set-daily \\',
        '"${VENV_DIR}/bin/fqdn-updater" schedule install --config "${CONFIG_PATH}"',
        "stage_preserved_paths",
        "restore_preserved_paths",
        'find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +',
    ]:
        assert text in install_script, text

    assert "Ubuntu 24.04 only" not in install_script
    assert "noble stable" not in install_script
    assert "archive/refs/tags/${release_tag}.tar.gz" not in install_script


def test_install_script_preserves_existing_docker_runtime() -> None:
    install_script = _read("install.sh")

    runtime_check_start = install_script.index("docker_runtime_available()")
    install_start = install_script.index("install_docker_packages()")
    install_end = install_script.index("resolve_release_metadata()")
    runtime_check_block = install_script[runtime_check_start:install_start]
    install_block = install_script[install_start:install_end]

    assert "docker compose version" in runtime_check_block
    assert "if docker_runtime_available; then" in install_block
    assert "systemctl enable --now docker" in install_block
    assert "return" in install_block
    assert install_block.index("if docker_runtime_available; then") < install_block.index(
        "install_docker_repository"
    )
    assert "apt_install docker-buildx-plugin docker-compose-plugin" in install_block
    assert (
        "apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin "
        "docker-compose-plugin" in install_block
    )


def test_install_script_resolves_release_metadata_without_main_fallback() -> None:
    install_script = _read("install.sh")

    resolve_start = install_script.index("resolve_release_metadata()")
    download_start = install_script.index("download_release_tarball()")
    resolve_block = install_script[resolve_start:download_start]

    assert '"${GITHUB_API_URL}/releases/tags/${RELEASE_VERSION}"' in resolve_block
    assert '"${GITHUB_API_URL}/releases/latest"' in resolve_block
    assert "2>/dev/null" in resolve_block
    assert '|| fail "Cannot resolve latest GitHub Release for ${REPOSITORY_SLUG}."' in resolve_block
    assert '|| fail "Cannot resolve GitHub Release for tag ${RELEASE_VERSION}."' in resolve_block
    assert '|| fail "Cannot parse GitHub Release asset metadata."' in resolve_block
    assert 'payload.get("tag_name")' in resolve_block
    assert 'payload.get("assets")' in resolve_block
    assert 'asset.get("browser_download_url")' in resolve_block
    assert 'tarball_name = f"fqdn-updater-{tag_name}.tar.gz"' in resolve_block
    assert 'checksum_name = f"{tarball_name}.sha256"' in resolve_block
    assert "print(asset_urls[tarball_name])" in resolve_block
    assert "print(asset_urls[checksum_name])" in resolve_block
    assert 'RESOLVED_RELEASE_TAG="${release_metadata[0]}"' in resolve_block
    assert 'RELEASE_TARBALL_URL="${release_metadata[1]}"' in resolve_block
    assert 'RELEASE_CHECKSUM_URL="${release_metadata[2]}"' in resolve_block
    assert "GitHub Release tag mismatch" in resolve_block
    assert "heads/" not in resolve_block
    assert "DEFAULT_BRANCH" not in resolve_block
    assert "git ls-remote" not in resolve_block
    assert "archive/refs/tags" not in resolve_block


def test_install_script_verifies_checksum_before_extracting_and_deploying() -> None:
    install_script = _read("install.sh")

    verify_start = install_script.index("verify_release_checksum()")
    download_start = install_script.index("download_release_tarball()")
    deploy_start = install_script.index("prepare_install_root()")
    main_start = install_script.index("main()")
    verify_block = install_script[verify_start:download_start]
    download_block = install_script[download_start:deploy_start]
    main_block = install_script[main_start:]

    assert "Checksum asset ${archive_name}.sha256 is missing or malformed." in verify_block
    assert "sha256sum --check --status" in verify_block
    assert "Checksum verification failed for ${archive_name}." in verify_block

    assert "--retry 5" in download_block
    assert "--retry-all-errors" in download_block
    assert "fqdn-updater-${release_tag}.tar.gz" in download_block
    assert "${archive_name}.sha256" in download_block
    assert '"${RELEASE_TARBALL_URL}"' in download_block
    assert '"${RELEASE_CHECKSUM_URL}"' in download_block
    assert '|| fail "Cannot download release asset ${archive_name}."' in download_block
    assert '|| fail "Cannot download release checksum asset ${checksum_name}."' in download_block
    assert 'verify_release_checksum "${archive_path}" "${checksum_path}" "${archive_name}"' in (
        download_block
    )
    assert '|| fail "Cannot extract release asset ${archive_name}."' in download_block
    assert '[[ -f "${extract_dir}/pyproject.toml" ]]' in download_block
    assert 'DOWNLOAD_RELEASE_DIR="${extract_dir}"' in download_block

    assert download_block.index('"${RELEASE_TARBALL_URL}"') < download_block.index(
        '"${RELEASE_CHECKSUM_URL}"'
    )
    assert download_block.index('"${RELEASE_CHECKSUM_URL}"') < download_block.index(
        "verify_release_checksum"
    )
    assert download_block.index("verify_release_checksum") < download_block.index("tar -xzf")

    assert "archive/refs/tags" not in download_block
    assert "archive/refs/heads" not in download_block
    assert "release_ref" not in main_block
    assert "deploy_release" not in download_block

    assert "resolve_release_metadata" in main_block
    assert "download_release_tarball" in main_block
    assert 'deploy_release "${DOWNLOAD_RELEASE_DIR}"' in main_block
    assert 'install_wrapper "${RESOLVED_RELEASE_TAG}"' in main_block
    assert main_block.index("resolve_release_metadata") < main_block.index(
        "download_release_tarball"
    )
    assert main_block.index("download_release_tarball") < main_block.index("deploy_release")


def test_runtime_code_does_not_use_python_3_11_datetime_utc_alias() -> None:
    for path in (ROOT / "src").rglob("*.py"):
        assert "datetime import UTC" not in path.read_text(encoding="utf-8"), path


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
    wrapper_start = install_script.index("install_wrapper()")
    main_start = install_script.index("main()")
    wrapper_block = install_script[wrapper_start:main_start]
    run_update_start = wrapper_block.index("run_update()")
    run_update_end = wrapper_block.index('cd "${INSTALL_DIR}"')
    run_update_block = wrapper_block[run_update_start:run_update_end]

    for text in [
        "sync|dry-run|status)",
        'exec docker compose run --rm fqdn-updater "${command_name}" "$@"',
        'readonly LOCAL_INSTALLER="${INSTALL_DIR}/install.sh"',
        "printf 'readonly REINSTALL_RELEASE_TAG=%q\\n'",
        "For Ubuntu 22.04 or later, reinstall from a versioned release tag with:",
        "https://raw.githubusercontent.com/Spiceman161/fqdn-updater/%s/install.sh",
        "update)",
        'run_update "$@"',
        '[[ ! -r "${LOCAL_INSTALLER}" ]]',
        'temp_copy="$(mktemp)"',
        'cp "${LOCAL_INSTALLER}" "${temp_copy}"',
        'chmod 0700 "${temp_copy}"',
        'bash "${temp_copy}" "$@"',
        'sudo bash "${temp_copy}" "$@"',
        'rm -f "${temp_copy}"',
        'exit "${status}"',
        "panel|init|config|router|mapping|schedule)",
        'exec "${VENV_CLI}" "${command_name}" "$@"',
    ]:
        assert text in wrapper_block, text

    assert "readonly INSTALLER_URL" not in wrapper_block
    assert "raw.githubusercontent.com/Spiceman161/fqdn-updater/main" not in wrapper_block
    assert "main/install.sh" not in wrapper_block
    assert "curl -fsSL" not in run_update_block

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


def test_install_script_installs_wrapper_with_resolved_release_tag() -> None:
    install_script = _read("install.sh")

    wrapper_start = install_script.index("install_wrapper()")
    main_start = install_script.index("main()")
    wrapper_block = install_script[wrapper_start:main_start]

    assert 'local reinstall_release_tag="$1"' in wrapper_block
    assert '[[ -n "${reinstall_release_tag}" ]]' in wrapper_block
    assert "printf 'readonly REINSTALL_RELEASE_TAG=%q\\n'" in wrapper_block
    assert "from fqdn_updater import __version__; print(__version__)" not in install_script
    assert "resolve_wrapper_reinstall_tag" not in install_script


def test_operator_docs_do_not_document_main_installer_path() -> None:
    for relative_path in [
        "README.md",
        "README_EN.md",
        "docs/DEPLOYMENT.md",
        "docs/USER_QUICKSTART.md",
        "docs/LLM_CONTEXT.md",
    ]:
        text = _read(relative_path)
        assert "/main/install.sh" not in text, relative_path
        assert "archive/refs/heads/main" not in text, relative_path
