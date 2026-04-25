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

resolve_release_version() {
    if [[ -n "${RELEASE_VERSION}" ]]; then
        printf '%s\n' "${RELEASE_VERSION}"
        return
    fi

    if curl \
        -fsSL \
        -H "Accept: application/vnd.github+json" \
        -H "User-Agent: fqdn-updater-installer" \
        "${GITHUB_API_URL}/releases/latest" \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["tag_name"])'; then
        return
    fi

    local fallback_version
    fallback_version="$(git ls-remote --tags --refs "https://github.com/${REPOSITORY_SLUG}.git" \
        | awk '{ sub("refs/tags/", "", $2); print $2 }' \
        | sort -V \
        | tail -n 1)"
    [[ -n "${fallback_version}" ]] || fail "No GitHub Release or git tag found for ${REPOSITORY_SLUG}."
    printf '%s\n' "${fallback_version}"
}

download_release_tarball() {
    local version="$1"
    local archive_path="${TEMP_DIR}/release.tar.gz"
    local extract_dir="${TEMP_DIR}/release"

    mkdir -p "${extract_dir}"
    curl -fsSL "https://github.com/${REPOSITORY_SLUG}/archive/refs/tags/${version}.tar.gz" -o "${archive_path}"
    tar -xzf "${archive_path}" -C "${extract_dir}" --strip-components=1

    printf '%s\n' "${extract_dir}"
}

prepare_install_root() {
    install -d -m 0755 "${INSTALL_DIR}"
    install -d -m 0755 "${INSTALL_DIR}/data"
    install -d -m 0700 "${INSTALL_DIR}/secrets"
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

build_runtime_image() {
    (
        cd "${INSTALL_DIR}"
        docker compose build fqdn-updater
    )
}

install_wrapper() {
    cat > "${WRAPPER_PATH}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

readonly INSTALL_DIR="/opt/fqdn-updater"
readonly VENV_CLI="${INSTALL_DIR}/.venv/bin/fqdn-updater"

cd "${INSTALL_DIR}"

if [[ $# -eq 0 ]]; then
    exec "${VENV_CLI}" panel
fi

command_name="$1"
shift

case "${command_name}" in
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

    local version
    version="$(resolve_release_version)"
    local release_dir
    release_dir="$(download_release_tarball "${version}")"

    deploy_release "${release_dir}"
    install_virtualenv
    initialize_config_if_missing
    install_schedule
    build_runtime_image
    install_wrapper

    printf 'fqdn-updater %s installed in %s\n' "${version}" "${INSTALL_DIR}"
}

main "$@"
