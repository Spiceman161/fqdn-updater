# Contributing

Thanks for improving FQDN-updater. Keep changes small, auditable, and aligned with the product
scope in `PRD.md` and `ARCHITECTURE.md`.

## Development Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

## Verification

Before opening a pull request, run:

```bash
./scripts/verify.sh
```

This is the same gate used by CI.

## Architecture Rules

- Keep core business logic in Python.
- Preserve the `cli`, `application`, `domain`, and `infrastructure` boundaries.
- Keep RCI/HTTP details inside infrastructure adapters.
- Preserve managed-only and read-before-write behavior for every apply path.
- Do not introduce a web UI, daemon, SSH transport, notifications, or non-Keenetic support unless
  the roadmap explicitly calls for it.

## Pull Requests

- Use focused commits with Conventional Commit messages.
- Include tests for behavior changes.
- Update docs when the operator workflow changes.
- Never commit real `config.json`, `.env*`, `secrets/`, or runtime `data/`.
