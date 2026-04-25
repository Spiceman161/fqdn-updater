# FQDN-updater Architecture

**Date:** 2026-04-25  
**Status:** Design document  
**Related:** [PRD.md](PRD.md)

## 1. Назначение

FQDN-updater — Python-first CLI batch tool для синхронизации managed FQDN object-group и route binding на Keenetic-роутерах. Целевой transport один: KeenDNS RCI API по HTTPS с HTTP Digest Auth.

SSH не является production transport, fallback или скрытым режимом. Любое добавление другого transport требует отдельного изменения PRD/architecture.

## 2. Принципы

- **Single-purpose job:** запуск как one-shot command, без daemon.
- **CLI-first:** все функции доступны из CLI, панель остаётся CLI layer.
- **Managed-only:** меняются только object-group и route binding из managed mappings.
- **Read-before-write:** любой apply сначала читает router state и строит diff.
- **RCI boundary:** HTTP Digest Auth, RCI payloads, retries и save-config живут только в infrastructure client.
- **Failure isolation:** ошибка одного роутера не останавливает остальные.
- **Artifacts:** каждый run оставляет JSON artifact и лог.

## 3. Runtime context

```text
systemd timer
  -> docker compose run --rm fqdn-updater sync
  -> load config and secrets
  -> fetch upstream raw lists
  -> normalize sources
  -> read Keenetic state through RCI
  -> compute deterministic diff
  -> apply managed changes when mode=sync
  -> write logs and artifacts
```

## 4. Layers

```text
src/fqdn_updater/
  cli/
  application/
  domain/
  infrastructure/
```

### CLI

- Typer commands.
- Rich terminal panel.
- Human/JSON output formatting.
- Exit code wiring.

### Application

- Use-case orchestration.
- Dry-run and sync run flows.
- Config management service.
- Route target discovery.
- Status diagnostics.

### Domain

- Typed config models.
- Object-group and route binding diff.
- Source normalization contracts.
- Keenetic limits and sharding policy.
- Run artifact models.

### Infrastructure

- Keenetic RCI client.
- Raw source fetcher.
- Config persistence.
- Secret resolving.
- Run artifacts/logs.
- systemd installer.

## 5. Transport boundary

Application code depends on a typed Keenetic client interface. It must not know:

- HTTP request shape;
- Digest Auth details;
- RCI JSON command batches;
- retry/backoff details;
- save-config command syntax.

The infrastructure RCI client owns those details and returns typed domain/application models.

## 6. Sync flow

```text
CLI dry-run/sync
  -> validate config
  -> load secret env file
  -> acquire run lock
  -> load enabled service sources
  -> for each enabled router:
       -> resolve secret
       -> create RCI client
       -> for each managed mapping:
            -> read object-group and route state
            -> compute object-group and route diffs
            -> if dry-run: record only
            -> if sync: apply minimal changes
       -> if sync changed router: save Keenetic config
  -> write artifact and logs
  -> return stable exit code
```

## 7. Managed object-groups

The planner builds desired entries from normalized sources and config mappings. It must:

- preserve deterministic ordering;
- reject plans above total Keenetic FQDN limits before write;
- shard service lists above one-group limit into deterministic managed group names;
- remove stale managed shard routes when a list shrinks;
- never mutate groups not represented by managed mappings.

## 8. Route bindings

Route bindings are managed per mapping:

- `route_target_type=interface` uses interface target.
- `route_target_type=gateway` uses gateway target and optional interface.
- `auto` and `exclusive` are explicit mapping flags.
- Existing unrelated routes are ignored.

## 9. Secrets

Secrets do not live in `config.json` as plaintext. Supported references:

- `password_env`;
- `password_file`.

The panel writes generated passwords to `.env.secrets` and stores only the env key in config. Logs and artifacts must redact secrets.

## 10. Scheduling

The app remains a batch tool. `runtime.schedule` stores operator intent. `schedule install` renders host-level systemd service/timer that runs Docker Compose once per trigger.

## 11. Error handling

- Config validation failure is fatal for the run.
- Source failure marks affected services failed and continues where safe.
- Router failure marks that router failed and continues other routers.
- Mapping failure marks that service/mapping failed and continues where safe.
- Lock failure returns a dedicated exit code.

Exit code groups:

- `0` success without changes;
- `10` sync success with changes;
- `20` partial/failed router or service;
- `30` dry-run found changes;
- `40` invalid config/fatal startup;
- `50` lock/concurrency issue.

## 12. Public files

Public repository contents include product code, tests, examples, docs, license, and CI. Runtime files, local configs, secrets, AI workflow state and reference artifacts are ignored.

## 13. Testing

Tests mirror `src/` structure:

- domain tests for config, diff, sharding and source normalization;
- application tests for dry-run/sync orchestration and config workflows;
- infrastructure tests for RCI parsing/apply behavior, repositories, scheduler and secrets;
- CLI/panel tests for user-facing flows.

Final local gate is always:

```bash
./scripts/verify.sh
```

## 14. Extension path

Allowed future extensions must preserve RCI-only production behavior unless PRD changes:

- richer `doctor` diagnostics;
- point sync for one router/service;
- stronger retry/cache policies;
- release packaging improvements;
- optional notification layer outside the core sync engine.
