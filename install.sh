#!/usr/bin/env bash
set -euo pipefail

readonly REPOSITORY_OWNER="Spiceman161"
readonly REPOSITORY_NAME="fqdn-updater"
readonly REPOSITORY_SLUG="${REPOSITORY_OWNER}/${REPOSITORY_NAME}"
readonly GITHUB_API_URL="https://api.github.com/repos/${REPOSITORY_SLUG}"
readonly INSTALL_DIR="/opt/fqdn-updater"
readonly VENV_DIR="${INSTALL_DIR}/.venv"
readonly CONFIG_PATH="${INSTALL_DIR}/config.json"
readonly WRAPPER_PATH="/usr/local/bin/fqdn-updater"
readonly ALIAS_PATH="/usr/local/bin/domaingo"

RELEASE_VERSION=""
TEMP_DIR=""
DOWNLOAD_RELEASE_DIR=""
RESOLVED_RELEASE_TAG=""
RELEASE_TARBALL_URL=""
RELEASE_CHECKSUM_URL=""

cleanup() {
    if [[ -n "${TEMP_DIR}" && -d "${TEMP_DIR}" ]]; then
        rm -rf "${TEMP_DIR}"
    fi
}

usage() {
    cat <<'EOF'
Usage: install.sh [--version <tag>]

Install or update fqdn-updater into /opt/fqdn-updater.

Options:
  --version <tag>  Install a specific GitHub Release tag instead of the latest release.
  -h, --help       Show this help message.
EOF
}

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 1
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --version)
                [[ $# -ge 2 ]] || fail "--version requires a tag value."
                RELEASE_VERSION="$2"
                [[ -n "${RELEASE_VERSION}" ]] || fail "--version requires a non-empty tag value."
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                fail "Unknown argument: $1"
                ;;
        esac
    done
}

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        fail "Run as root or via sudo."
    fi
}

require_ubuntu_22_or_later() {
    if [[ ! -r /etc/os-release ]]; then
        fail "Cannot detect operating system."
    fi

    # shellcheck disable=SC1091
    . /etc/os-release

    if [[ "${ID:-}" != "ubuntu" ]]; then
        fail "This installer supports Ubuntu 22.04 and later only."
    fi

    local version_major="${VERSION_ID%%.*}"
    local version_minor="${VERSION_ID#*.}"
    version_minor="${version_minor%%.*}"

    if [[ ! "${version_major}" =~ ^[0-9]+$ || ! "${version_minor}" =~ ^[0-9]+$ ]]; then
        fail "Cannot detect supported Ubuntu version."
    fi

    if (( version_major < 22 || (version_major == 22 && version_minor < 4) )); then
        fail "This installer supports Ubuntu 22.04 and later only."
    fi
}

require_systemd() {
    if ! command -v systemctl >/dev/null 2>&1; then
        fail "systemctl is required."
    fi

    if [[ ! -d /run/systemd/system ]]; then
        fail "systemd is required."
    fi
}

apt_install() {
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$@"
}

install_base_packages() {
    apt-get update
    apt_install ca-certificates curl git gnupg python3 python3-venv
}

install_docker_repository() {
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ -n "${VERSION_CODENAME:-}" ]] || fail "Cannot detect Ubuntu codename."

    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/ubuntu/gpg" -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    local architecture
    architecture="$(dpkg --print-architecture)"

    cat > /etc/apt/sources.list.d/docker.list <<EOF
deb [arch=${architecture} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable
EOF
}

docker_runtime_available() {
    command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1
}

install_docker_packages() {
    if docker_runtime_available; then
        systemctl enable --now docker
        return
    fi

    install_docker_repository
    apt-get update

    if command -v docker >/dev/null 2>&1; then
        apt_install docker-buildx-plugin docker-compose-plugin
    else
        apt_install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi

    systemctl enable --now docker
    docker_runtime_available || fail "Docker with Compose plugin is required."
}

resolve_release_metadata() {
    local release_json

    if [[ -n "${RELEASE_VERSION}" ]]; then
        release_json="$(curl \
            -fsSL \
            -H "Accept: application/vnd.github+json" \
            -H "User-Agent: fqdn-updater-installer" \
            "${GITHUB_API_URL}/releases/tags/${RELEASE_VERSION}" \
            2>/dev/null)" \
            || fail "Cannot resolve GitHub Release for tag ${RELEASE_VERSION}."
    else
        release_json="$(curl \
            -fsSL \
            -H "Accept: application/vnd.github+json" \
            -H "User-Agent: fqdn-updater-installer" \
            "${GITHUB_API_URL}/releases/latest" \
            2>/dev/null)" \
            || fail "Cannot resolve latest GitHub Release for ${REPOSITORY_SLUG}."
    fi

    local parsed_release
    parsed_release="$(python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(1)

tag_name = payload.get("tag_name")
if not isinstance(tag_name, str):
    sys.exit(1)

tag_name = tag_name.strip()
if not tag_name:
    sys.exit(1)

tarball_name = f"fqdn-updater-{tag_name}.tar.gz"
checksum_name = f"{tarball_name}.sha256"
required_names = {tarball_name, checksum_name}
asset_urls = {}

assets = payload.get("assets")
if not isinstance(assets, list):
    sys.exit(1)

for asset in assets:
    if not isinstance(asset, dict):
        continue
    name = asset.get("name")
    url = asset.get("browser_download_url")
    if name not in required_names:
        continue
    if not isinstance(url, str):
        sys.exit(1)
    url = url.strip()
    if not url:
        sys.exit(1)
    asset_urls[name] = url

if required_names - asset_urls.keys():
    sys.exit(1)

print(tag_name)
print(asset_urls[tarball_name])
print(asset_urls[checksum_name])
' <<< "${release_json}")" \
        || fail "Cannot parse GitHub Release asset metadata."

    local -a release_metadata
    mapfile -t release_metadata <<< "${parsed_release}"
    [[ "${#release_metadata[@]}" -eq 3 ]] \
        || fail "Cannot parse GitHub Release asset metadata."

    RESOLVED_RELEASE_TAG="${release_metadata[0]}"
    RELEASE_TARBALL_URL="${release_metadata[1]}"
    RELEASE_CHECKSUM_URL="${release_metadata[2]}"

    if [[ -n "${RELEASE_VERSION}" && "${RESOLVED_RELEASE_TAG}" != "${RELEASE_VERSION}" ]]; then
        fail "GitHub Release tag mismatch: expected ${RELEASE_VERSION}, got ${RESOLVED_RELEASE_TAG}."
    fi
}

verify_release_checksum() {
    local archive_path="$1"
    local checksum_path="$2"
    local archive_name="$3"

    local expected_checksum
    expected_checksum="$(python3 -c '
from pathlib import Path
import re
import sys

try:
    lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
except Exception:
    sys.exit(1)

checksum_lines = [line.strip() for line in lines if line.strip()]
if len(checksum_lines) != 1:
    sys.exit(1)

checksum = checksum_lines[0].split()[0]
if re.fullmatch(r"[0-9A-Fa-f]{64}", checksum) is None:
    sys.exit(1)

print(checksum.lower())
' "${checksum_path}")" \
        || fail "Checksum asset ${archive_name}.sha256 is missing or malformed."

    printf '%s  %s\n' "${expected_checksum}" "${archive_path}" \
        | sha256sum --check --status \
        || fail "Checksum verification failed for ${archive_name}."
}

download_release_tarball() {
    local release_tag="${RESOLVED_RELEASE_TAG}"
    local archive_name="fqdn-updater-${release_tag}.tar.gz"
    local checksum_name="${archive_name}.sha256"
    local archive_path="${TEMP_DIR}/${archive_name}"
    local checksum_path="${TEMP_DIR}/${checksum_name}"
    local extract_dir="${TEMP_DIR}/release"

    mkdir -p "${extract_dir}"
    curl --fail --silent --show-error --location \
        --retry 5 \
        --retry-delay 2 \
        --retry-all-errors \
        "${RELEASE_TARBALL_URL}" \
        -o "${archive_path}" \
        || fail "Cannot download release asset ${archive_name}."
    curl --fail --silent --show-error --location \
        --retry 5 \
        --retry-delay 2 \
        --retry-all-errors \
        "${RELEASE_CHECKSUM_URL}" \
        -o "${checksum_path}" \
        || fail "Cannot download release checksum asset ${checksum_name}."
    verify_release_checksum "${archive_path}" "${checksum_path}" "${archive_name}"
    tar -xzf "${archive_path}" -C "${extract_dir}" --strip-components=1 \
        || fail "Cannot extract release asset ${archive_name}."
    [[ -f "${extract_dir}/pyproject.toml" ]] \
        || fail "Downloaded archive does not contain pyproject.toml."

    DOWNLOAD_RELEASE_DIR="${extract_dir}"
}

prepare_install_root() {
    install -d -m 0755 "${INSTALL_DIR}"
    install -d -m 0755 "${INSTALL_DIR}/data"
    install -d -m 0700 "${INSTALL_DIR}/secrets"
    if [[ ! -e "${INSTALL_DIR}/.env.secrets" ]]; then
        install -m 0600 /dev/null "${INSTALL_DIR}/.env.secrets"
    fi
}

stage_preserved_paths() {
    local preserve_dir="$1"

    mkdir -p "${preserve_dir}"

    if [[ -e "${CONFIG_PATH}" ]]; then
        mv "${CONFIG_PATH}" "${preserve_dir}/config.json"
    fi

    local env_path
    for env_path in "${INSTALL_DIR}"/.env*; do
        [[ -e "${env_path}" ]] || continue
        mv "${env_path}" "${preserve_dir}/$(basename "${env_path}")"
    done

    local path_name
    for path_name in data secrets .venv; do
        if [[ -e "${INSTALL_DIR}/${path_name}" ]]; then
            mv "${INSTALL_DIR}/${path_name}" "${preserve_dir}/${path_name}"
        fi
    done
}

restore_preserved_paths() {
    local preserve_dir="$1"

    local preserved_path
    for preserved_path in "${preserve_dir}"/* "${preserve_dir}"/.env* "${preserve_dir}"/.venv; do
        [[ -e "${preserved_path}" ]] || continue
        mv "${preserved_path}" "${INSTALL_DIR}/$(basename "${preserved_path}")"
    done
}

clean_install_root() {
    find "${INSTALL_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
}

remove_preserved_paths_from_release() {
    local release_dir="$1"

    rm -rf \
        "${release_dir}/config.json" \
        "${release_dir}/data" \
        "${release_dir}/secrets" \
        "${release_dir}/.venv"
    find "${release_dir}" -mindepth 1 -maxdepth 1 -name '.env*' -exec rm -rf {} +
}

deploy_release() {
    local release_dir="$1"
    local preserve_dir

    prepare_install_root
    preserve_dir="$(mktemp -d "${INSTALL_DIR}.preserve.XXXXXX")"
    stage_preserved_paths "${preserve_dir}"
    clean_install_root
    remove_preserved_paths_from_release "${release_dir}"
    cp -a "${release_dir}/." "${INSTALL_DIR}/"
    restore_preserved_paths "${preserve_dir}"
    rmdir "${preserve_dir}"
    prepare_install_root
}

install_virtualenv() {
    python3 -m venv "${VENV_DIR}"
    "${VENV_DIR}/bin/pip" install --upgrade pip
    (
        cd "${INSTALL_DIR}"
        "${VENV_DIR}/bin/pip" install --upgrade .
    )
}

initialize_config_if_missing() {
    if [[ -f "${CONFIG_PATH}" ]]; then
        return
    fi

    "${VENV_DIR}/bin/fqdn-updater" init --config "${CONFIG_PATH}"
    "${VENV_DIR}/bin/fqdn-updater" schedule set-daily \
        --config "${CONFIG_PATH}" \
        --time 03:15 \
        --timezone Europe/Moscow
}

install_schedule() {
    "${VENV_DIR}/bin/fqdn-updater" schedule install --config "${CONFIG_PATH}"
}

set_config_permissions() {
    if [[ -f "${CONFIG_PATH}" ]]; then
        chmod 0644 "${CONFIG_PATH}"
    fi
}

build_runtime_image() {
    (
        cd "${INSTALL_DIR}"
        docker compose build fqdn-updater
    )
}

install_wrapper() {
    local reinstall_release_tag="$1"
    [[ -n "${reinstall_release_tag}" ]] || fail "Cannot install wrapper without a release tag."

    cat > "${WRAPPER_PATH}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

readonly INSTALL_DIR="/opt/fqdn-updater"
readonly VENV_CLI="${INSTALL_DIR}/.venv/bin/fqdn-updater"
readonly LOCAL_INSTALLER="${INSTALL_DIR}/install.sh"
EOF
    printf 'readonly REINSTALL_RELEASE_TAG=%q\n' "${reinstall_release_tag}" >> "${WRAPPER_PATH}"
    cat >> "${WRAPPER_PATH}" <<'EOF'

print_reinstall_command() {
    printf 'For Ubuntu 22.04 or later, reinstall from a versioned release tag with:\n' >&2
    printf 'curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/%s/install.sh | sudo bash -s -- --version %s\n' \
        "${REINSTALL_RELEASE_TAG}" \
        "${REINSTALL_RELEASE_TAG}" \
        >&2
}

run_update() {
    if [[ ! -r "${LOCAL_INSTALLER}" ]]; then
        printf 'Error: Local installer %s is missing or unreadable.\n' "${LOCAL_INSTALLER}" >&2
        print_reinstall_command
        exit 1
    fi

    if [[ "${EUID}" -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
        printf 'Error: sudo is required to update fqdn-updater. Re-run as root.\n' >&2
        exit 1
    fi

    local temp_copy
    temp_copy="$(mktemp)"
    trap 'rm -f "${temp_copy}"' EXIT
    cp "${LOCAL_INSTALLER}" "${temp_copy}"
    chmod 0700 "${temp_copy}"

    local status
    set +e
    if [[ "${EUID}" -eq 0 ]]; then
        bash "${temp_copy}" "$@"
    else
        sudo bash "${temp_copy}" "$@"
    fi
    status=$?
    set -e

    rm -f "${temp_copy}"
    trap - EXIT
    exit "${status}"
}

cd "${INSTALL_DIR}"

if [[ $# -eq 0 ]]; then
    exec "${VENV_CLI}" panel
fi

command_name="$1"
shift

case "${command_name}" in
    update)
        run_update "$@"
        ;;
    sync|dry-run|status)
        exec docker compose run --rm fqdn-updater "${command_name}" "$@"
        ;;
    panel|init|config|router|mapping|schedule)
        exec "${VENV_CLI}" "${command_name}" "$@"
        ;;
    *)
        exec "${VENV_CLI}" "${command_name}" "$@"
        ;;
esac
EOF

    chmod 0755 "${WRAPPER_PATH}"
    ln -sfn "${WRAPPER_PATH}" "${ALIAS_PATH}"
}

main() {
    trap cleanup EXIT
    parse_args "$@"
    require_root
    require_ubuntu_22_or_later
    require_systemd

    TEMP_DIR="$(mktemp -d)"

    install_base_packages
    install_docker_packages

    resolve_release_metadata
    download_release_tarball

    deploy_release "${DOWNLOAD_RELEASE_DIR}"
    install_virtualenv
    initialize_config_if_missing
    set_config_permissions
    install_schedule
    build_runtime_image
    install_wrapper "${RESOLVED_RELEASE_TAG}"

    printf 'fqdn-updater %s installed in %s\n' "${RESOLVED_RELEASE_TAG}" "${INSTALL_DIR}"
}

main "$@"
