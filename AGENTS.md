# Repository Guidelines

## Project identity
FQDN-updater is a production-oriented CLI tool for centrally synchronizing managed FQDN object-groups on multiple Keenetic routers.

Current product scope:
- Python-first CLI tool
- Keenetic routers only
- Remote access via KeenDNS RCI API over HTTPS only
- HTTP Digest Auth with a low-privilege API user
- Batch execution via Docker and systemd timer
- No web UI, no notifications, no sharding in MVP

This repository must implement the PRD and ARCHITECTURE docs as the source of truth for product and architecture behavior.

---

## Architecture rules (non-negotiable)

### Python-first
- All core business logic MUST live in Python.
- Shell scripts are allowed ONLY for thin workflow/bootstrap glue.
- Do NOT move domain logic into bash.
- Do NOT rely on manual tmux steps as product behavior.

### Layering
Every feature SHOULD follow this layered structure where practical:

- cli
- application
- domain
- infrastructure

Layer responsibilities:
- cli = Typer commands, output formatting, exit code wiring
- application = orchestration use cases and run flows
- domain = entities, diff rules, validation contracts, policies
- infrastructure = RCI client, source fetching, config persistence, artifact writing

### Transport boundary
- RCI API details MUST stay behind a dedicated Keenetic client/infrastructure layer.
- HTTP Digest Auth, payload shapes, retries, and save-config calls must not leak into domain logic.
- Business logic must operate on typed models, not raw HTTP payloads.

### Managed-only updates
- The tool must change only explicitly managed object-groups and route bindings.
- No broad config mutation on routers.
- No hidden side effects outside the selected mappings.

### Read-before-write
- Any apply path must read current router state first.
- Diff must be deterministic.
- No blind overwrite strategy.

### Scope restrictions for current stage
Unless the task explicitly requires otherwise:
- do not add a web UI
- do not add notifications
- do not add sharding for large lists
- do not add non-Keenetic support
- do not add SSH transport to the production path
- focus on scaffold, contracts, adapters, and safe sync behavior

---

## Required project structure

Use this top-level structure inside `src/fqdn_updater/`:

- `cli`
- `application`
- `domain`
- `infrastructure`

Keep files small and single-purpose.

Expected supporting roots:
- `tests/`
- `examples/`
- `data/` (runtime artifacts, usually gitignored)
- `docs/`

---

## Existing repository conventions

### Edit autonomy
- Inside this repository workspace, do not ask for confirmation before non-destructive file edits that are required to complete the accepted task.
- Ask before destructive actions, network actions that need approval, or writes outside the workspace.
- Do not treat sandbox as disabled; assume normal sandbox restrictions and request escalation only when truly required.

### Tooling and commands
Preferred baseline commands:

- `python -m fqdn_updater --help` or installed `fqdn-updater --help`
- `pytest`
- `ruff check .`
- `ruff format .`
- `./scripts/verify.sh`

If a project-local wrapper script is added later, prefer that single entrypoint for final verification.

### Style
- Python 3.12+
- explicit types where useful
- small functions
- predictable naming
- no hidden magic
- file names in `snake_case.py`
- classes in `UpperCamelCase`
- methods and variables in `lower_snake_case` only when required by Python conventions; otherwise keep normal Python style (`lower_case_with_underscores`)

### Testing
Mirror `src/` structure under `tests/`.
Name test files with `test_*.py`.

Test ownership rules for Codex workflow:
- implementation worker must not claim test ownership unless explicitly instructed
- `test_builder` is the default owner for adding/updating tests and final verification
- final green status must be backed by `./scripts/verify.sh`
- `./scripts/verify.sh` is the source of truth for the slice verification set

### Commits and PRs
Use Conventional Commits:
- `feat:`
- `fix:`
- `refactor:`
- `test:`
- `chore:`
- `docs:`

---

## Codex workflow for this repository

Use a slice-oriented workflow for non-trivial work.

### Required lifecycle

1. The main orchestrator should start with `slice_preparer` for roadmap-driven work.
2. `slice_preparer` reads:
   - `PRD.md`
   - `ARCHITECTURE.md`
3. `slice_preparer` picks the next minimal valid slice and prepares:
   - orchestration brief
   - planning-gate input
   - implementation-ready English worker prompt
4. The main orchestrator must do a planning pass before implementation delegation.
5. After plan agreement, implementation is delegated to `python_worker`.
6. After implementation, the orchestrator runs `test_builder`.
7. After tests/checks, the orchestrator runs `slice_gatekeeper` and any specialist reviewers relevant to the slice.
8. If the slice is green, report in Russian:
   - what changed
   - why it matters
   - what to verify manually
   - a short codex/tmux-adapted verification instruction if useful
9. Only after explicit user approval may `finisher`:
   - update progress docs if present
   - create a Conventional Commit
   - push to the configured tracking branch

### Verification gate
- No slice is green until `./scripts/verify.sh` passes.
- `test_builder` must use `./scripts/verify.sh` for final verification, not an ad hoc command list.
- CI must run `./scripts/verify.sh` on every push and pull request.

### Language rules
- User-facing planning and reporting: Russian.
- Worker implementation prompt: English.
- Reviews: either language, but findings must be concrete.

### Human gate
Never skip the human approval gate before finish/push.
Never skip the planning gate before worker delegation.

---

## How Codex must operate in this repository

When implementing something:
1. choose the smallest useful change-set
2. respect cli/application/domain/infrastructure boundaries
3. keep the code production-oriented and easy to audit
4. avoid speculative abstractions
5. prefer explicit adapters and typed models over clever shortcuts

Before writing code, check:
- Is the code in the correct layer?
- Are HTTP/RCI details leaking into application or domain?
- Is the change the smallest useful step?
- Does it preserve managed-only and read-before-write guarantees?

When reviewing code, prioritize:
1. safety of router mutations
2. PRD/architecture violations
3. boundary leaks
4. incorrect diff/apply behavior
5. verification gaps

Avoid:
- introducing a daemon when a batch job is enough
- putting product logic into shell scripts
- hidden state and magic side effects
- inventing behavior not defined by the PRD/architecture
