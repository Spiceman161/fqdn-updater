# LLM Context

Этот файл — короткий контекст для LLM/агентов, которые меняют FQDN-updater. Он не заменяет `README.md`, `PRD.md` и `ARCHITECTURE.md`; при конфликте проверяйте код и тесты.

## Проект

FQDN-updater — Python CLI для безопасной синхронизации managed записей на Keenetic через KeenDNS RCI API:

- FQDN entries попадают в managed `object-group fqdn`;
- DNS-proxy route bindings связывают managed object-group с interface/gateway;
- CIDR entries попадают в managed static routes с comment prefix `fqdn-updater:<service>`.

Production transport только один: KeenDNS RCI по HTTPS с HTTP Digest Auth. SSH, web UI, daemon, notifications и не-Keenetic устройства вне текущего scope.

## Архитектура

Код разделён на слои:

- `cli/` — Typer commands, Rich/questionary panel, human/JSON output, exit codes.
- `application/` — use cases: config management, source loading, dry-run/sync orchestration, status, schedule, run history.
- `domain/` — Pydantic models, diff logic, source normalization, sharding, Keenetic limits, run artifacts.
- `infrastructure/` — RCI transport/client, parsers/commands, config persistence, secrets, logs/artifacts, systemd installer.

Правило: application/domain не должны знать форму HTTP payloads, Digest Auth, retries или RCI command batches. Эти детали остаются в infrastructure.

## Главные контракты

- `config.json` содержит `routers`, `services`, `mappings`, `runtime`.
- Enabled router должен иметь ровно один secret source: `password_env` или `password_file`.
- `rci_url` хранится как HTTPS endpoint с path `/rci/`.
- Service может использовать новый формат `sources` или legacy `source_urls` + `format`, но не оба сразу.
- Source formats: `raw_domain_list`, `raw_cidr_list`, `mixed`.
- Domain suffix filters работают только для domain sources.
- `sync` применяет только mappings с `managed=true`.
- `dry-run` не пишет на роутер.
- Любой apply делает read-before-write и пишет минимальный diff.
- FQDN object-group shard size — 300 entries, общий managed FQDN лимит на роутер — 1024 entries.
- CIDR entries не пишутся в FQDN object-groups; они планируются как static routes.
- Secrets не должны попадать в logs, artifacts или docs.

## Runtime

Installer ставит проект в `/opt/fqdn-updater`.

- `config.json` — операторский конфиг.
- `.env.secrets` или `.env` — env secrets.
- `secrets/` — file secrets для `password_file`.
- `data/artifacts` — JSON run artifacts.
- `data/logs` — run logs.
- `data/state` — локальное state/cache.

Host wrapper запускает `sync`, `dry-run` и `status` через Docker Compose. Команды управления (`panel`, `init`, `config`, `router`, `mapping`, `schedule`) идут через локальный Python venv.

Production install/update использует только GitHub Release tags. `fqdn-updater update` запускает локальный `/opt/fqdn-updater/install.sh` через временную копию и без `--version` ставит latest GitHub Release. Если latest release нельзя определить или metadata некорректны, installer должен завершиться с ошибкой до скачивания кода проекта; fallback на `main` отсутствует. Если локальный installer отсутствует или недоступен для чтения, wrapper должен завершиться с ошибкой и показать versioned reinstall command для Ubuntu 22.04+.

## Команды

Основной gate:

```bash
./scripts/verify.sh
```

Полезные read-only проверки:

```bash
.venv/bin/fqdn-updater --help
.venv/bin/fqdn-updater config validate --config config.json
.venv/bin/fqdn-updater dry-run --config config.json --output json
.venv/bin/fqdn-updater status --config config.json --output json
```

Не коммитьте production `config.json`, `.env*`, `secrets/`, `data/`, `.codex/` и другие локальные workflow state.

## Документация

При изменении операторского поведения обновляйте:

- `README.md` и при необходимости `README_EN.md`;
- соответствующий документ в `docs/`;
- `ARCHITECTURE.md` или `PRD.md`, если меняется scope, transport, runtime или public contract;
- packaging/docs tests, если меняется документационный контракт.
