# PRD: FQDN-updater

**Version:** 1.3  
**Date:** 2026-04-25  
**Status:** Design document

## 1. Обзор

FQDN-updater — production-oriented CLI-инструмент для централизованной синхронизации managed FQDN object-group на роутерах Keenetic с удалённого VPS.

Проект использует только KeenDNS RCI API по HTTPS с HTTP Digest Authentication и отдельным low-privilege пользователем. SSH/CLI transport не входит в MVP, не является fallback и не должен появляться в production path без отдельного изменения scope.

## 2. Цели

- Автоматизировать безопасное обновление FQDN/CIDR service lists на нескольких Keenetic-роутерах.
- Давать оператору `dry-run` с понятным diff до применения.
- Не выполнять лишних записей, если desired и actual state совпадают.
- Писать machine-readable run artifacts и человекочитаемые логи.
- Работать как одноразовый batch job из Docker Compose под systemd timer.

## 3. Пользователь

Технически уверенный администратор, который управляет несколькими Keenetic-роутерами, VPN-маршрутизацией и DNS-based routes. Основная боль — ручное обновление сервисных списков на множестве устройств.

## 4. Scope v1

In scope:

- Python CLI.
- Terminal panel для локального обслуживания config/secrets/runs.
- JSON config.
- Keenetic-only remote access через KeenDNS RCI API.
- HTTP Digest Auth.
- Low-privilege API user.
- Runtime raw-URL source registry для `itdoginfo/allow-domains`.
- Mixed domain/IPv4/IPv6 source loading.
- Managed object-group diff/apply.
- Managed route binding diff/apply.
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
- Пустые строки, комментарии и дубликаты отбрасываются.
- Домены нормализуются для детерминированного сравнения.
- CIDR entries поддерживаются там, где это нужно для сервиса.
- При failure одного source сервис помечается failed без порчи локального state.
- Репозиторий не вендорит upstream lists; хранится только URL mapping.

### Router inventory

- Конфиг хранит routers, services, mappings и runtime settings.
- CLI и панель позволяют добавить/изменить роутер без ручного JSON editing.
- Для роутера задаются `rci_url`, `username`, `password_env` или `password_file`, `enabled`, timeout и audit tags.
- `rci_url` хранится как внешний HTTPS endpoint вида `https://rci.<domain>/rci/`.

### Keenetic state read

- Перед apply updater читает actual object-group и route binding state.
- Ошибки чтения одного роутера не останавливают остальные роутеры.
- HTTP/RCI details скрыты за infrastructure client boundary.

### Diff/apply

- Diff содержит `to_add`, `to_remove`, `unchanged`.
- Если изменений нет, write-команды не отправляются.
- Apply меняет только mappings с `managed=true`.
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

## 6. Preconditions

- На Keenetic включён DNS proxy.
- Клиенты, для которых нужны DNS-based routes, используют Keenetic как DNS.
- Клиенты находятся в подходящей policy.
- Целевой interface/gateway доступен.
- KeenDNS publication для RCI настроена как HTTP Proxy на локальный порт `79`, внешний endpoint используется по HTTPS.
- Для RCI используется отдельный пользователь, например `api_updater`, с минимальными правами.

## 7. Nonfunctional Requirements

- Python 3.12+.
- Layering: `cli`, `application`, `domain`, `infrastructure`.
- Business logic работает с typed models, не с raw HTTP payloads.
- Deterministic ordering в diff, artifacts и human output.
- Atomic config writes.
- No hidden remote writes in read-only commands.
- `./scripts/verify.sh` — обязательный verification gate.

## 8. Data Model

- `Router`: `id`, `name`, `rci_url`, `username`, `password_env`, `password_file`, `enabled`, `tags`, `timeout_seconds`, `allowed_source_ips`.
- `ServiceDefinition`: `key`, `sources`, `enabled`, `description`.
- `RouterServiceMapping`: `router_id`, `service_key`, `object_group_name`, `route_target_type`, `route_target_value`, `route_interface`, `exclusive`, `auto`, `managed`.
- `RunArtifact`: `run_id`, timestamps, `trigger`, `mode`, `status`, router/service results.

## 9. Risks

| Risk | Mitigation |
| --- | --- |
| Upstream list disappears or changes format | Explicit source map, source failure status, no blind deletion on load failure |
| Diff bug removes valid entries | Tests, deterministic dry-run, managed-only policy |
| Public KeenDNS endpoint is attacked | Low-privilege user, long password, optional IP whitelist |
| Large lists exceed Keenetic limits | Pre-apply limit validation and deterministic sharding |
| DNS-based routes are configured but ineffective | Documentation and `status` diagnostics |

## 10. Future Possibilities

- `doctor` mode with richer Keenetic diagnostics.
- Point sync by router/service.
- More source policies for aggregate lists.
- Optional notifications.
- Public release packaging improvements.
