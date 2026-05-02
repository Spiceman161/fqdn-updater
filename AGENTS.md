# Repository Guidelines

## Project Identity

FQDN-updater is a Python CLI batch tool for safely synchronizing managed FQDN
object-groups, DNS-proxy route bindings, and CIDR static routes on Keenetic
routers.

Current product scope:
- Keenetic-only remote access.
- KeenDNS RCI API over HTTPS with HTTP Digest Auth.
- Terminal CLI and Rich/questionary panel, no web UI.
- One-shot commands and Docker Compose jobs under a systemd timer, no daemon.
- Managed FQDN object-groups, managed DNS route bindings, and managed static
  routes only.

`PRD.md`, `ARCHITECTURE.md`, and `docs/LLM_CONTEXT.md` are the source of truth
for product scope and architecture. If these disagree with code or tests, read
the code and tests before changing behavior.

## Architecture Rules

Keep the existing layered structure:
- `src/fqdn_updater/cli` contains Typer commands, Rich/questionary panel flows,
  human/JSON output, and exit-code wiring.
- `src/fqdn_updater/application` contains use cases and orchestration.
- `src/fqdn_updater/domain` contains typed models, validation, diff logic,
  source normalization, sharding, Keenetic limits, and run artifacts.
- `src/fqdn_updater/infrastructure` contains RCI transport/client details,
  command/parsing adapters, persistence, secrets, logs/artifacts, cache, and
  systemd integration.

Application and domain code must not know HTTP payload shapes, Digest Auth,
retry/backoff details, RCI command batches, or `save-config` command syntax.
Those details belong behind infrastructure adapters.

## Non-Negotiable Behavior

- Production transport is only KeenDNS RCI over HTTPS with HTTP Digest Auth.
- Do not add SSH, non-Keenetic support, a web UI, notifications, or a daemon
  unless the PRD and architecture are changed intentionally.
- Every apply path must read current router state before writing.
- `status`, `dry-run`, panel checks, and run-history views must remain read-only.
- `sync` applies only mappings with `managed=true`.
- Writes must be scoped to managed object-groups, route bindings, and static
  routes declared by config mappings.
- FQDN entries go to object-groups; CIDR entries go to managed static routes
  with comment prefix `fqdn-updater:<service>`.
- Source load failures must not cause unsafe deletion from incomplete desired
  state.
- Secrets must never be written to logs, run artifacts, docs, or committed files.

## Config And Runtime Contracts

`config.json` contains `routers`, `services`, `mappings`, and `runtime`.

Important contracts:
- Enabled routers use exactly one secret reference: `password_env` or
  `password_file`.
- `rci_url` is an HTTPS endpoint with path `/rci/`.
- Services use either `sources` or legacy `source_urls` plus `format`, not both.
- Source formats are `raw_domain_list`, `raw_cidr_list`, and `mixed`.
- Domain suffix filters apply only to domain sources.
- FQDN object-group shard size is 300 entries.
- Total managed FQDN entries per router must stay within the Keenetic 1024-entry
  section limit before any write.
- Runtime paths are resolved relative to the directory containing `config.json`
  unless absolute.

Do not commit production `config.json`, `config.*.json`, `.env*`, `secrets/`,
runtime `data/`, local AI/editor state, or reference artifacts.

## Development Commands

Set up a local environment with:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

Use the full local verification gate before finishing non-trivial changes:

```bash
./scripts/verify.sh
```

The gate runs:
- `ruff format . --check`
- `ruff check .`
- `python -m pytest`

Useful focused commands:

```bash
.venv/bin/fqdn-updater --help
.venv/bin/fqdn-updater config validate --config config.json
.venv/bin/fqdn-updater dry-run --config config.json --output json
.venv/bin/fqdn-updater status --config config.json --output json
```

## Style

- Target Python 3.10+.
- Use the existing typed Pydantic/domain model style.
- Keep changes focused and auditable.
- Prefer repository-local helpers and existing patterns over new abstractions.
- Keep CLI-facing output stable unless the task explicitly changes it.
- Use `ruff` formatting and imports.

## Testing

Tests mirror the source layers:
- `tests/domain` for models, validation, source normalization, diffing,
  sharding, schedules, and run artifacts.
- `tests/application` for orchestration and use cases.
- `tests/infrastructure` for RCI parsing/apply behavior, repositories,
  scheduling, secrets, logs, and cache.
- `tests/cli` for Typer commands and panel flows.
- `tests/packaging` for installation/runtime/docs contracts.

Add or update tests when behavior, contracts, parsing, orchestration, CLI output,
or packaging assumptions change. For documentation-only changes, run at least a
syntax/whitespace check when practical and call out if the full gate was skipped.

## Documentation

When operator behavior, config schema, CLI output, runtime files, scheduling,
scope, or architecture changes, update the relevant docs:
- `README.md` and, for README-level behavior, `README_EN.md`.
- The matching document under `docs/`.
- `docs/LLM_CONTEXT.md` for architecture, scope, runtime, or key contract
  changes.
- `ARCHITECTURE.md` or `PRD.md` when public scope, transport, runtime, or core
  contracts change.
- Packaging/docs tests if they assert the changed documentation contract.

Russian documentation is canonical for detailed operator guides.

## Git Workflow

- Use focused Conventional Commit messages such as `feat:`, `fix:`,
  `refactor:`, `test:`, `docs:`, or `chore:`.
- Do not revert unrelated user changes.
- Do not push unless the user explicitly asks for it.
