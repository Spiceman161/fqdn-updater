# FQDN-updater

[![Verify](https://github.com/Spiceman161/fqdn-updater/actions/workflows/verify.yml/badge.svg?branch=main)](https://github.com/Spiceman161/fqdn-updater/actions/workflows/verify.yml)

FQDN-updater is a production-oriented Python CLI for centrally synchronizing managed
FQDN object-groups on Keenetic routers through the KeenDNS RCI API.

The tool is built for a small VPS or home-server deployment: configure routers and service
lists locally, preview changes with `dry-run`, apply only managed object-groups and route
bindings with `sync`, and run the same one-shot job from Docker Compose under a systemd timer.

## Features

- Keenetic-only remote access through KeenDNS RCI over HTTPS.
- HTTP Digest Auth with a low-privilege API user.
- Managed-only updates: the tool changes only configured object-groups and route bindings.
- Read-before-write sync planning with deterministic diffs.
- Built-in service source registry and source normalization.
- `status`, `dry-run`, and `sync` commands for operator-safe verification.
- Rich terminal panel for local config maintenance.
- Docker Compose runtime and config-driven systemd timer installation.
- One-command Ubuntu 24.04 bootstrap installer.

## Safety Model

FQDN-updater is intentionally narrow:

- no web UI;
- no daemon process;
- no SSH transport in the production path;
- no router-wide config mutation;
- no hidden writes from read-only commands.

All RCI transport details stay behind the infrastructure client. Domain and application logic
operate on typed models rather than raw HTTP payloads.

## Installation

On a clean Ubuntu 24.04 host:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash
```

Install a specific release tag:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash -s -- --version v0.1.0
```

The installer deploys the selected release to `/opt/fqdn-updater`, preserves existing
`config.json`, `.env*`, `data/`, `secrets/`, and `.venv`, installs host commands
`fqdn-updater` and `domaingo`, builds the Docker image, and installs the systemd timer.

## First Run

Open the terminal panel:

```bash
fqdn-updater
```

Useful command-line checks:

```bash
fqdn-updater config validate --config /opt/fqdn-updater/config.json
fqdn-updater status --config /opt/fqdn-updater/config.json
fqdn-updater dry-run --config /opt/fqdn-updater/config.json
```

Apply managed changes:

```bash
fqdn-updater sync --config /opt/fqdn-updater/config.json
```

The host wrapper runs `sync`, `dry-run`, and `status` through Docker Compose. Management commands
such as `panel`, `init`, `config`, `router`, `mapping`, and `schedule` run through the local Python
virtual environment in `/opt/fqdn-updater/.venv`.

## Scheduling

Set a daily schedule and install systemd units:

```bash
fqdn-updater schedule set-daily --config /opt/fqdn-updater/config.json --time 03:15 --timezone Europe/Moscow
sudo fqdn-updater schedule install --config /opt/fqdn-updater/config.json
```

Inspect the timer:

```bash
systemctl status fqdn-updater.timer --no-pager
journalctl -u fqdn-updater.service -n 100 --no-pager
```

## Keenetic RCI Setup

For KeenDNS RCI access, use two different settings:

- in the Keenetic web UI, publish the `rci.<domain>` web application with protocol `HTTP` and
  port `79`;
- in `config.json`, store the external endpoint as `https://rci.<domain>/rci/`.

Use a dedicated low-privilege API user for FQDN-updater. Store real passwords in `.env` or
`secrets/`; do not commit secrets or production configs.

## Local Development

Requirements:

- Python 3.12+
- Docker and Docker Compose plugin for packaging/runtime checks

Set up a development environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

Run the verification gate:

```bash
./scripts/verify.sh
```

The same script runs in GitHub Actions on push and pull request.

## Documentation

- [User quickstart](docs/USER_QUICKSTART.md)
- [Product requirements](PRD.md)
- [Architecture](ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)

## Project Status

FQDN-updater is usable for managed Keenetic FQDN object-group and route-binding synchronization.
The deeper `doctor` diagnostics mode is not implemented yet.

## Security

Please report security issues privately. See [SECURITY.md](SECURITY.md).
