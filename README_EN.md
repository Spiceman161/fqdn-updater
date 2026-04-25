# FQDN-updater

English | [Русский](README.md)

[![Verify](https://github.com/Spiceman161/fqdn-updater/actions/workflows/verify.yml/badge.svg?branch=main)](https://github.com/Spiceman161/fqdn-updater/actions/workflows/verify.yml)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE)

**FQDN-updater** is a source-available CLI tool for centrally synchronizing managed FQDN object-groups on Keenetic routers through the KeenDNS RCI API.

It is built for a small VPS or home server: configure routers and service lists locally, preview changes with `dry-run`, apply only explicitly managed object-groups and route bindings with `sync`, and run the same one-shot job from Docker Compose under a systemd timer.

## Safety Model

- Keenetic only.
- KeenDNS RCI API over HTTPS only, with HTTP Digest Auth.
- Dedicated low-privilege API user for the published RCI web application.
- Every apply reads current router state first, builds a deterministic diff, and only then writes changes.
- The tool changes only object-groups and route bindings explicitly configured as managed.
- `status`, `dry-run`, the run history screen, and panel checks do not perform remote writes.
- The MVP does not include a web UI, daemon, notifications, non-Keenetic support, or production SSH transport.

## Installation

On a clean Ubuntu 22.04 or later host:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash
```

Install a specific release tag:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash -s -- --version v0.1.0
```

The installer deploys the project to `/opt/fqdn-updater`, preserves existing `config.json`, `.env*`, `data/`, `secrets/`, and `.venv`, installs host commands `fqdn-updater` and `domaingo`, builds the Docker image, and installs the systemd timer.

## First Run

Open the panel:

```bash
fqdn-updater
```

Alternative entry points:

```bash
domaingo
fqdn-updater panel --config /opt/fqdn-updater/config.json
```

The panel can create a config, add a router, generate an RCI user password, select service lists, discover WireGuard interfaces, and run `dry-run` before synchronization.

## Core Commands

Validate the config:

```bash
fqdn-updater config validate --config /opt/fqdn-updater/config.json
```

Check remote preconditions without router writes:

```bash
fqdn-updater status --config /opt/fqdn-updater/config.json
```

Preview the change plan:

```bash
fqdn-updater dry-run --config /opt/fqdn-updater/config.json
```

Apply managed changes:

```bash
fqdn-updater sync --config /opt/fqdn-updater/config.json
```

The host wrapper runs `sync`, `dry-run`, and `status` through Docker Compose. Management commands (`panel`, `init`, `config`, `router`, `mapping`, `schedule`) run through the local Python venv in `/opt/fqdn-updater/.venv`.

## Scheduling

Set a daily schedule and install systemd units:

```bash
fqdn-updater schedule set-daily --config /opt/fqdn-updater/config.json --time 03:15 --timezone Europe/Moscow
sudo fqdn-updater schedule install --config /opt/fqdn-updater/config.json
```

Inspect the timer and logs:

```bash
systemctl status fqdn-updater.timer --no-pager
journalctl -u fqdn-updater.service -n 100 --no-pager
```

## KeenDNS RCI

In the Keenetic web UI, publish the `rci.<domain>` web application with protocol `HTTP` and port `79`. In `config.json`, store the external endpoint as `https://rci.<domain>/rci/`.

Use a dedicated low-privilege user for FQDN-updater. Store real passwords in `.env`, `.env.secrets`, or `secrets/`; do not commit production `config.json` or secrets.

## Documentation

- [Operator quickstart](docs/USER_QUICKSTART.md)
- [Panel guide](docs/PANEL.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Configuration](docs/CONFIGURATION.md)
- [CLI reference](docs/CLI_REFERENCE.md)
- [KeenDNS RCI setup](docs/KEENETIC_RCI_SETUP.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Roadmap](docs/ROADMAP.md)
- [PRD](PRD.md)
- [Architecture](ARCHITECTURE.md)

## License

FQDN-updater is licensed under [PolyForm Noncommercial 1.0.0](LICENSE).

This is a source-available/noncommercial project, not OSI open source. Noncommercial use, study, and modification are allowed under the license terms; commercial use requires separate permission from the rights holder.

Third-party notices: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
