# PRD: FQDN-updater

**Version:** 1.4
**Date:** 2026-05-02
**Status:** Current product document

## 1. Обзор

FQDN-updater — production-oriented CLI-инструмент для централизованной синхронизации managed FQDN object-group, DNS-proxy route bindings и CIDR static routes на роутерах Keenetic с удалённого VPS или домашнего сервера.

Проект использует только KeenDNS RCI API по HTTPS с HTTP Digest Authentication и отдельным low-privilege пользователем. SSH/CLI transport не входит в scope, не является fallback и не должен появляться в production path без отдельного изменения scope.

## 2. Цели

- Автоматизировать безопасное обновление domain/CIDR service lists на нескольких Keenetic-роутерах.
- Давать оператору `dry-run` с понятным diff до применения.
- Не выполнять лишних записей, если desired и actual state совпадают.
- Писать machine-readable run artifacts и человекочитаемые логи.
- Работать как одноразовый batch job из Docker Compose под systemd timer.
- Оставаться удобным для людей и LLM через актуальную документацию и явные архитектурные контракты.

## 3. Пользователь

Технически уверенный администратор, который управляет несколькими Keenetic-роутерами, VPN-маршрутизацией и DNS-based routes. Основная боль — ручное обновление сервисных списков на множестве устройств.

## 4. Scope v1

In scope:

- Python CLI.
- Terminal panel для локального обслуживания config/secrets/runs/schedule.
- JSON config.
- Keenetic-only remote access через KeenDNS RCI API.
- HTTP Digest Auth.
- Low-privilege API user.
- Runtime raw-URL source registry для `itdoginfo/allow-domains`.
- Mixed domain/IPv4/IPv6 source loading.
- Domain suffix filters for derived services.
- Managed object-group diff/apply.
- Managed DNS route binding diff/apply.
- Managed static route diff/apply for CIDR entries.
- Read-before-write для любого apply.
- `status`, `dry-run`, `sync`.
- Docker Compose runtime.
- systemd timer.

Out of scope:

- Web UI.
- Уведомления.
- Поддержка не-Keenetic устройств.
- Production SSH transport.
- Долгоживущий daemon или self-hosted API.
- Автообнаружение роутеров в сети.
- Multi-user RBAC.

## 5. Functional Requirements

### Source loading

- Для каждого поддерживаемого сервиса updater загружает raw lists по URL.
- Сервисы могут использовать `sources` или legacy `source_urls` + `format`.
- Пустые строки, комментарии и дубликаты отбрасываются.
- Домены и CIDR entries нормализуются для детерминированного сравнения.
- Domain suffix filters применяются только к domain sources.
- При failure одного source сервис помечается failed без порчи локального state.
- Репозиторий не вендорит upstream lists; хранится только URL mapping.

### Router inventory

- Конфиг хранит routers, services, mappings и runtime settings.
- CLI и панель позволяют добавить/изменить роутер без ручного JSON editing.
- Для роутера задаются `rci_url`, `username`, `password_env` или `password_file`, `enabled`, timeout и audit tags.
- `rci_url` хранится как внешний HTTPS endpoint вида `https://rci.<domain>/rci/`.

### Keenetic state read

- Перед apply updater читает actual object-group, DNS route binding, static route и DNS proxy state.
- Ошибки чтения одного роутера не останавливают остальные роутеры.
- HTTP/RCI details скрыты за infrastructure client boundary.

### Diff/apply

- Diff содержит добавления, удаления, unchanged entries и route changes.
- Если изменений нет, write-команды не отправляются.
- Apply меняет только mappings с `managed=true`.
- FQDN entries пишутся в managed object-groups.
- CIDR entries пишутся в managed IPv4/IPv6 static routes с comment prefix `fqdn-updater:<service>`.
- Для RCI большие command batches должны чанковаться.
- После успешных write-изменений вызывается `system configuration save`.
- Списки выше 300 FQDN entries шардируются в deterministic managed groups; общий FQDN plan выше 1024 entries на роутер отклоняется до любой записи.

### Dry run

- `dry-run` не пишет на роутеры.
- Output доступен в human и JSON mode.
- Exit code различает clean run, changes found и errors.

### Logging and artifacts

- Каждый run пишет run artifact.
- Для каждого роутера и сервиса фиксируются status, counts, route changes и error details.
- Секреты не попадают в логи и artifacts.

### Scheduling

- systemd timer запускает одноразовый Docker Compose job.
- Расписание хранится в `runtime.schedule`.
- `schedule install` рендерит и устанавливает host-level unit/timer.

### Documentation

- `docs/README.md` является навигационным входом.
- Подробная документация ведётся на русском.
- `README_EN.md` поддерживается как краткий англоязычный обзор.
- `docs/LLM_CONTEXT.md` содержит компактный контекст для LLM/агентов.

## 6. Preconditions

- На Keenetic включён DNS proxy.
- Клиенты, для которых нужны DNS-based routes, используют Keenetic как DNS.
- Клиенты находятся в подходящей policy.
- Целевой interface/gateway доступен.
- KeenDNS publication для RCI настроена как HTTP Proxy на локальный порт `79`, внешний endpoint используется по HTTPS.
- Для RCI используется отдельный пользователь, например `api_updater`, с минимальными правами.

## 7. Nonfunctional Requirements

- Python package supports Python 3.10+; Docker image uses Python 3.12 slim.
- Layering: `cli`, `application`, `domain`, `infrastructure`.
- Business logic работает с typed models, не с raw HTTP payloads.
- Deterministic ordering в diff, artifacts и human output.
- Atomic config writes.
- No hidden remote writes in read-only commands.
- `./scripts/verify.sh` — обязательный verification gate.

## 8. Data Model

- `Router`: `id`, `name`, `rci_url`, `username`, `auth_method`, `password_env`, `password_file`, `enabled`, `tags`, `timeout_seconds`, `allowed_source_ips`.
- `ServiceDefinition`: `key`, `sources` или `source_urls` + `format`, `enabled`, `description`.
- `ServiceSource`: `url`, `format`, `include_domain_suffixes`, `exclude_domain_suffixes`.
- `RouterServiceMapping`: `router_id`, `service_key`, `object_group_name`, `route_target_type`, `route_target_value`, `route_interface`, `exclusive`, `auto`, `managed`.
- `Runtime`: `artifacts_dir`, `logs_dir`, `state_dir`, `secrets_env_file`, `log_format`, `schedule`.
- `RunArtifact`: `run_id`, timestamps, `trigger`, `mode`, `status`, router/service results.

## 9. Risks

| Risk | Mitigation |
| --- | --- |
| Upstream list disappears or changes format | Explicit source map, source failure status, no blind deletion on load failure |
| Diff bug removes valid entries | Tests, deterministic dry-run, managed-only policy |
| Public KeenDNS endpoint is attacked | Low-privilege user, long password, optional IP whitelist |
| Large lists exceed Keenetic limits | Pre-apply limit validation and deterministic sharding |
| DNS-based routes are configured but ineffective | Documentation and `status` diagnostics |
| Documentation drifts after refactors | Docs index, LLM context, packaging docs tests |

## 10. Future Possibilities

- `doctor` mode with richer Keenetic diagnostics.
- Point sync by router/service.
- More source policies for aggregate lists.
- Optional notifications.
- Public release packaging improvements.
