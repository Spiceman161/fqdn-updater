# Contributing

Thanks for improving FQDN-updater. Keep changes small, auditable, and aligned with the product scope in `PRD.md` and `ARCHITECTURE.md`.

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
- Keep `status`, `dry-run`, panel checks and run history read-only.
- Do not introduce a web UI, daemon, SSH transport, notifications, or non-Keenetic support unless the roadmap and PRD explicitly call for it.

## Documentation Rules

- Update docs when operator workflow, config schema, CLI output, runtime files, scheduling or architecture changes.
- Russian docs are canonical for detailed guides.
- Keep `README_EN.md` as a synchronized English overview when README-level behavior changes.
- Update `docs/LLM_CONTEXT.md` when architecture, scope, runtime or key contracts change.
- Do not document local secrets, production configs, `.codex/`, `.agents/`, `data/` contents or private artifacts.

## Pull Requests

- Use focused commits with Conventional Commit messages.
- Include tests for behavior changes.
- Update packaging/docs tests when documentation contracts change.
- Never commit real `config.json`, `.env*`, `secrets/`, runtime `data/`, or local AI/editor state.
