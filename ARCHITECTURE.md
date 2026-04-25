# FQDN-updater Architecture
**Date:** 2026-04-06  
**Status:** Draft
**Related:** `./PRD.md`

---

## 1. Purpose

Этот документ описывает целевую архитектуру проекта **FQDN-updater** — CLI-инструмента, который запускается на VPS, получает актуальные сервисные FQDN-списки из внешнего источника и синхронизирует их с FQDN object-group на нескольких Keenetic-роутерах. Поскольку роутеры находятся за серыми IP, архитектура должна опираться **только на KeenDNS RCI API по HTTPS** с **HTTP Digest Auth**. SSH не рассматривается ни как основной, ни как fallback transport для целевой эксплуатации проекта.

Архитектура ориентирована на:
- простой и надёжный MVP;
- эксплуатацию через Docker + systemd timer;
- OpenClaw-friendly управление;
- расширение в будущем без переписывания ядра.

---

## 2. Architectural Principles

### 2.1 Single-purpose job
Приложение не является daemon-сервисом. Оно запускается как **одноразовый batch job**:
- старт;
- загрузка конфига;
- загрузка upstream-данных;
- sync по роутерам;
- запись артефактов;
- завершение.

### 2.2 CLI-first
Вся функциональность должна быть доступна через CLI. JSON-конфиг — это внутренний persistent state, а не основной UX.

### 2.3 Managed-only changes
Updater должен изменять **только те object-group и route binding**, которые явно описаны в конфиге как управляемые.

### 2.4 Read-before-write
Любое изменение начинается с чтения текущего состояния на роутере, затем строится diff, и только потом применяются минимальные изменения.

### 2.5 RCI-only transport boundary
Бизнес-логика не должна зависеть от деталей HTTP-запросов, Digest Auth и RCI payload. Эти детали должны быть инкапсулированы в одном transport/API слое.

### 2.6 Failure isolation
Ошибка на одном роутере или в одной группе не должна останавливать обработку остальных.

### 2.7 Machine-readable artifacts
Каждый run должен оставлять JSON-артефакт, пригодный для OpenClaw, скриптов, health-check и ручного аудита.

---

## 3. High-Level System Context

```text
                   +------------------------------+
                   |        itdoginfo source      |
                   |  raw .lst service lists      |
                   +--------------+---------------+
                                  |
                                  v
+--------------------+   +--------+---------+   +-----------------------+
| systemd timer      |-->| Docker job       |-->| local config/state    |
| on VPS             |   | fqdn-updater     |   | JSON + logs + reports |
+--------------------+   +--------+---------+   +-----------------------+
                                  |
                                  v
                    +-----------------------------+
                    | RCI API via KeenDNS / SSH   |
                    | transport abstraction layer |
                    +------+------+------+--------+
                           |      |      |
                           v      v      v
                       Router A Router B Router N
```

---

## 4. Runtime Model

### 4.1 Execution chain
Целевой сценарий v1:

```text
systemd timer
  -> systemd service
    -> docker compose run --rm fqdn-updater sync
      -> app loads config
      -> app fetches sources
      -> app connects to routers via RCI API
      -> app computes diffs
      -> app applies changes (or dry-run)
      -> app writes logs + summary
      -> exit code returned to systemd
```

### 4.2 Why not daemon
Daemon здесь не нужен, потому что:
- нет real-time требований;
- расписание естественно решается systemd timer;
- проще отлаживать единичные прогоны;
- проще обновлять контейнер и конфиг;
- ниже операционная сложность.

---

## 5. Main Components

### 5.1 CLI Layer
Отвечает за входную точку приложения.

Предполагаемые команды:
- `fqdn-updater init`
- `fqdn-updater config validate`
- `fqdn-updater router add`
- `fqdn-updater router list`
- `fqdn-updater mapping set`
- `fqdn-updater sync`
- `fqdn-updater dry-run`
- `fqdn-updater status`

Responsibilities:
- парсинг аргументов;
- выбор output mode (human/json);
- выбор конфига;
- запуск orchestration layer;
- определение exit code.

### 5.2 Config Manager
Работает с JSON-конфигом и валидирует его.

Responsibilities:
- загрузка config JSON;
- schema validation;
- нормализация defaults;
- безопасная запись изменений при CLI-командах;
- защита от частично записанного файла.

Критично: запись конфига должна быть atomic, например через temp file + rename.

### 5.3 Source Registry
Хранит mapping между логическими сервисами/категориями и upstream-URL.

Встроенные сервисы v1:
- `anime`
- `block`
- `geoblock`
- `hodca`
- `news`
- `porn`
- `cloudflare`
- `cloudfront`
- `digitalocean`
- `discord`
- `google_ai`
- `google_meet`
- `google_play`
- `hdrezka`
- `hetzner`
- `meta`
- `ovh`
- `roblox`
- `telegram`
- `tiktok`
- `twitter`
- `youtube`

Responsibilities:
- знать source URL;
- знать source format;
- подключать category-списки из `Categories/<category>.lst`;
- объединять `Services/<service>.lst` с соответствующими `Subnets/IPv4/<service>.lst` и
  `Subnets/IPv6/<service>.lst`, если subnet-файлы есть для этого сервиса;
- вернуть unified service definition.

### 5.4 Source Fetcher
Отвечает за загрузку upstream-списков.

Responsibilities:
- HTTP GET raw lists;
- поддержка нескольких source URL на один логический сервис;
- базовая retry logic в будущем;
- timeout handling;
- понятные ошибки при 404/429/timeout;
- передача сырого содержимого в normalizer.

### 5.5 Normalizer
Превращает сырой upstream-текст в canonical entry set.

Responsibilities:
- trim whitespace;
- ignore empty lines;
- ignore comments;
- lowercase where applicable;
- различать FQDN и CIDR/подсети;
- deduplicate;
- return sorted canonical list/set.

Важно: архитектура должна допускать смешанные object-group (FQDN + CIDR), потому что для некоторых сервисов это practically useful.

### 5.6 Router Orchestrator
Главный управляющий слой выполнения sync.

Responsibilities:
- инициализация run context;
- последовательная обработка роутеров;
- накопление run results;
- управление dry-run/apply mode;
- делегирование SSH и diff/apply логики.

### 5.7 Transport Layer and Keenetic Clients
Vendor-specific слой, инкапсулирующий transport и API/CLI особенности Keenetic.

Responsibilities:
- выбрать реализацию transport по конфигу;
- открыть HTTPS/RCI session или SSH session;
- выполнить команды;
- собрать response/stdout/stderr;
- скрыть детали transport и протокола от остального приложения.

Целевой интерфейс должен предоставлять абстрактные операции вроде:
- `get_object_group(name)`
- `ensure_object_group(name)`
- `add_entries(name, items)`
- `remove_entries(name, items)`
- `ensure_route(mapping)`
- `save_config()`
- `get_dns_proxy_status()`

Предполагаемые реализации:
- `KeeneticRciClient` — primary path для внешнего VPS через `https://rci.{name}.keenetic.pro/rci/` + HTTP Digest Auth.
  В Keenetic web application для этого 4-го уровня используется HTTP upstream к локальному RCI
  порту `79`; HTTPS относится к внешнему клиентскому соединению updater -> KeenDNS endpoint.
- `KeeneticSshClient` — fallback path для локального/прямого доступа.

Для SSH-клиента использовать актуальный синтаксис Keenetic CLI:
- `object-group fqdn <name>`
- `include <address>`
- `exclude <address>`
- `show object-group fqdn [<group>]`
- `show dns-proxy`
- `dns-proxy route object-group <group> (( <gateway> [ <interface> ]) | <interface> ) [ auto ] [ reject ]`
- `no route [ object-group <group> ] [ <interface> ]`

Для RCI-клиента использовать:
- `POST https://rci.{name}.keenetic.pro/rci/`
- KeenDNS HTTP Proxy web application: protocol `HTTP`, upstream port `79`
- HTTP Digest Auth
- JSON array of RCI commands
- явный `system configuration save` после успешного apply

### 5.8 Diff Engine
Сравнивает desired state и actual state.

Output:
- `to_add`
- `to_remove`
- `unchanged`

Responsibilities:
- детерминированный diff;
- отсутствие ложных изменений;
- возможность сериализовать diff в JSON и human output.
- валидация лимитов Keenetic до apply: максимум 300 записей в одном `object-group fqdn` и
  максимум 1024 записи в управляемом FQDN-плане роутера.
- автоматическое разбиение desired state на managed shard-группы по 300 записей с именами
  `base`, `base-2`, `base-3`, `base-4`.

### 5.9 Apply Engine
Применяет diff к роутеру.

Responsibilities:
- корректный порядок операций;
- минимальный набор CLI-команд;
- stop-on-group-error, continue-on-router-loop;
- фиксация результата каждого шага.

Рекомендуемый порядок:
1. убедиться, что object-group существует;
2. получить actual state;
3. вычислить diff;
4. удалить устаревшие записи;
5. добавить новые записи;
6. проверить/обновить route binding;
7. для stale shard-групп удалить route binding;
8. если используется RCI и были изменения — сохранить конфигурацию;
9. записать результат.

Для RCI-пути нужно предусмотреть chunking больших apply-пакетов, чтобы не отправлять чрезмерно большие POST batch.
Apply не должен начинаться, если desired state нарушает общий лимит Keenetic FQDN:
1024 записи суммарно по управляемым FQDN-группам роутера. Списки больше 300 записей должны
разбиваться на shard-группы до apply.

### 5.10 Logging and Artifact Writer
Записывает лог и итоговые JSON-отчёты.

Responsibilities:
- human-readable log lines;
- structured JSON summary;
- отдельный артефакт на каждый run;
- стабильный формат для OpenClaw.

---

## 6. Directory Layout

Целевая структура проекта:

```text
fqdn-updater/
├── README.md
├── pyproject.toml
├── Dockerfile
├── compose.yaml
├── .gitignore
├── src/
│   └── fqdn_updater/
│       ├── __init__.py
│       ├── cli.py
│       ├── exit_codes.py
│       ├── constants.py
│       ├── config/
│       │   ├── models.py
│       │   ├── manager.py
│       │   └── schema.py
│       ├── sources/
│       │   ├── registry.py
│       │   ├── fetcher.py
│       │   └── normalize.py
│       ├── routers/
│       │   ├── orchestrator.py
│       │   ├── diff.py
│       │   ├── apply.py
│       │   └── results.py
│       ├── keenetic/
│       │   ├── base.py
│       │   ├── rci_client.py
│       │   ├── ssh_client.py
│       │   ├── commands.py
│       │   ├── parser.py
│       │   └── models.py
│       ├── logging/
│       │   ├── setup.py
│       │   └── formatters.py
│       └── artifacts/
│           ├── writer.py
│           └── models.py
├── tests/
│   ├── test_normalize.py
│   ├── test_diff.py
│   ├── test_config.py
│   └── test_keenetic_parser.py
├── examples/
│   ├── config.example.json
│   ├── fqdn-updater.service
│   └── fqdn-updater.timer
└── data/
    ├── config/
    ├── logs/
    ├── state/
    └── runs/
```

---

## 7. Configuration Architecture

### 7.1 Configuration files
Минимум для v1:
- `config.json` — основной конфиг;
- `state/` — локальные служебные файлы;
- `runs/` — run summaries;
- `logs/` — текстовые или JSON-логи.

### 7.2 Proposed config shape

```json
{
  "version": 1,
  "defaults": {
    "timeout_seconds": 10,
    "output_format": "human"
  },
  "routers": [
    {
      "id": "home-spb",
      "name": "Home Keenetic",
      "rci_url": "https://rci.example.keenetic.pro",
      "username": "api_updater",
      "auth_method": "digest",
      "password_env": "KEENETIC_PASS_HOME_SPB",
      "enabled": true,
      "mappings": [
        {
          "service_key": "telegram",
          "object_group_name": "svc_telegram",
          "route_target_type": "interface",
          "route_target_value": "Wireguard0",
          "auto": true,
          "exclusive": true,
          "managed": true
        }
      ]
    }
  ]
}
```

### 7.3 Config mutation strategy
CLI-команды должны модифицировать JSON сами, чтобы человек не возился со структурой вручную.

Примеры:
- `router add`
- `router remove`
- `mapping set`
- `mapping remove`
- `config validate`

---

## 8. State and Artifacts

### 8.1 Persistent state
В v1 локальный state должен быть минимальным.

Что можно хранить:
- last successful run metadata;
- source fetch timestamps;
- hash последнего upstream content по сервису;
- lock file для защиты от параллельных запусков.

### 8.2 Run summary format
Каждый run создаёт JSON-файл вроде:

```json
{
  "run_id": "2026-04-06T14:00:00Z-abc123",
  "mode": "apply",
  "started_at": "2026-04-06T14:00:00Z",
  "finished_at": "2026-04-06T14:00:42Z",
  "status": "partial",
  "routers": [
    {
      "router_id": "home-spb",
      "status": "updated",
      "services": [
        {
          "service_key": "telegram",
          "status": "updated",
          "added_count": 2,
          "removed_count": 1,
          "unchanged_count": 17
        }
      ]
    }
  ]
}
```

### 8.3 Log format
Для v1 достаточно двух форматов:
- human log для чтения человеком;
- JSON summary artifact для машин.

При желании можно позже добавить JSON line logging.

---

## 9. Data Flow

## 9.1 Sync flow

```text
CLI sync/dry-run
  -> load config
  -> validate config
  -> build run context
  -> fetch all enabled sources
  -> normalize source lists
  -> for each router:
       -> connect via configured transport (RCI or SSH)
       -> for each managed mapping:
            -> read object-group state
            -> compute diff
            -> if dry-run: record diff only
            -> if apply: apply changes + ensure route
            -> if transport=RCI and changes were made: save config
       -> collect router status
  -> write run summary
  -> exit with final code
```

## 9.2 Read path

```text
Keenetic Client
  -> transport-specific read (RCI or SSH)
  -> parse response/output
  -> return canonical set of entries
```

## 9.3 Write path

```text
Diff Engine result
  -> remove obsolete entries
  -> add missing entries
  -> ensure route object-group binding
  -> optional verification readback (future or selective)
```

---

## 10. Keenetic Integration Design

### 10.1 Integration boundary
Вся логика, связанная с transport/API/CLI особенностями Keenetic, должна жить в модуле `keenetic/`.

Это важно, чтобы:
- diff engine не знал про RCI payload или CLI строки;
- бизнес-логика не зависела от формата ответа RCI или вывода `show object-group fqdn`;
- возможные изменения синтаксиса были локализованы.

### 10.2 Required operations
Обязательные операции v1:
- проверить доступность RCI endpoint;
- считать object-group;
- создать object-group при отсутствии;
- добавить entry;
- удалить entry;
- создать/обновить route binding;
- сохранить конфигурацию после успешного RCI apply;
- при необходимости считать диагностическое состояние DNS proxy.

### 10.3 Parsing concerns
Нужно предусмотреть, что:
- вывод Keenetic CLI может меняться между версиями;
- RCI response schema тоже может иметь нюансы реализации;
- роутер может возвращать и домены, и runtime-resolved IP.

Поэтому parser/adapter должен:
- доставать именно управляемые entries;
- не путать их с runtime-resolved IP;
- уметь различать FQDN и CIDR;
- иметь тесты на sample outputs и sample RCI responses.

### 10.4 Preconditions not guaranteed by app
Приложение не может гарантировать на клиентских устройствах:
- что у клиентов единственный DNS = роутер;
- что клиент в `Default policy`;
- что браузер не использует DoH.

Эти условия должны быть:
- явно задокументированы;
- позже проверяться через `doctor` или отдельный check mode, где возможно.

---

## 11. Error Handling Strategy

### 11.1 Error classes
Ошибки делятся на уровни:

1. **Fatal run errors**
   - конфиг невалиден;
   - lock already held;
   - container filesystem problem.

2. **Source errors**
   - upstream недоступен;
   - 404;
   - timeout;
   - invalid content.

3. **Router/transport errors**
   - Digest auth failed;
   - SSH auth failed;
   - KeenDNS endpoint unreachable;
   - host unreachable;
   - command/API call failed;
   - parsing failed.

4. **Mapping errors**
   - object-group invalid;
   - unsupported route target;
   - entry rejected by router.

### 11.2 Isolation rules
- fatal config error -> весь run завершается;
- source error по одному сервису -> остальные сервисы и роутеры продолжаются, если это безопасно;
- router error -> другие роутеры продолжаются;
- mapping error -> остальные mappings на этом же роутере могут продолжаться, если нет риска сломать state.

### 11.3 Exit code strategy
Примерная схема:
- `0` — success, no errors;
- `10` — success with changes applied;
- `20` — partial success / some routers failed;
- `30` — dry-run found changes;
- `40` — invalid config / fatal startup error;
- `50` — lock / concurrency issue.

Числа можно скорректировать, но градация должна быть стабильной.

---

## 12. Concurrency and Locking

### 12.1 MVP strategy
Внутри одного run — **последовательная обработка**.

Почему:
- проще отладка;
- ниже шанс race conditions;
- достаточно для первого релиза.

### 12.2 Inter-run protection
Нужен lock file, чтобы два schedule/manual запуска не шли одновременно.

Примеры:
- `data/state/run.lock`
- advisory lock через файловый дескриптор.

Если lock занят — приложение должно завершиться предсказуемым кодом и понятным сообщением.

---

## 13. Security Architecture

### 13.1 Secret handling
Секреты не должны храниться в JSON-конфиге в открытом виде.

Предпочтительно:
- Digest password через env var reference или mounted secret file;
- SSH key через mounted secret file;
- не хранить пароль `api_updater` в репозитории, примерах и логах.

### 13.2 RCI/KeenDNS security model
Для внешнего VPS базовый security profile должен быть таким:
- отдельный пользователь `api_updater`;
- только тег `http-proxy`;
- без admin rights;
- длинный случайный пароль (32+ символа);
- по возможности firewall whitelist HTTPS-доступа к KeenDNS endpoint только с IP VPS.

### 13.3 Container boundaries
Docker используется как изоляционный слой:
- отдельный runtime;
- минимальные зависимости;
- readonly image + writable volumes по возможности;
- no privileged mode.

### 13.4 Network model
Контейнеру нужен только исходящий доступ:
- к GitHub raw content;
- к KeenDNS RCI endpoint по HTTPS.

### 13.5 Change boundaries
Updater не должен:
- менять произвольные части конфигурации Keenetic;
- управлять политиками клиентов;
- включать/отключать DNS proxy автоматически без явного требования.

---

## 14. OpenClaw-Friendly Design

### 14.1 Why this matters
Проект должен быть удобен для управления агентом OpenClaw/"Крабом".

Это значит:
- минимум скрытого состояния;
- предсказуемые команды;
- хорошие exit codes;
- структурированные артефакты.

### 14.2 Requirements for agent control
- `dry-run` должен давать безопасный предварительный обзор.
- `sync` должен быть детерминированным.
- `status` должен читать последние артефакты и возвращать сводку.
- Логи должны быть пригодны для последующего анализа агентом.

### 14.3 Suggested future commands
Позже можно добавить:
- `fqdn-updater doctor`
- `fqdn-updater sync router <id>`
- `fqdn-updater sync service <service_key>`
- `fqdn-updater report latest --json`

---

## 15. Deployment Architecture

### 15.1 Docker deployment
Контейнер должен быть одноцелевым:
- содержит Python app;
- запускает CLI command;
- не поднимает долгоживущие сервисы.

### 15.2 Host mounts
Пример volumes:
- `./data/config:/app/data/config`
- `./data/logs:/app/data/logs`
- `./data/runs:/app/data/runs`
- `./data/state:/app/data/state`
- `./secrets:/run/secrets:ro`

### 15.3 systemd unit model
Host-level:
- `fqdn-updater.service` — one-shot run;
- `fqdn-updater.timer` — schedule trigger.

Это лучше cron, потому что:
- проще смотреть status;
- лучше логи;
- естественная интеграция с systemd ecosystem.

---

## 16. Testing Strategy

### 16.1 Unit tests
Покрыть обязательно:
- normalize;
- diff;
- config validation;
- Keenetic output parser.

### 16.2 Integration tests
На раннем этапе хотя бы:
- mock SSH transport;
- sample CLI outputs;
- apply command generation.

### 16.3 Manual validation
Перед production rollout:
- тест на одном роутере;
- тест на одной группе (`telegram` или `youtube`);
- `dry-run` -> `apply` -> повторный `dry-run` должен показать zero diff.

---

## 17. Extension Path

### 17.1 Near-term extensions
- backup before apply;
- upstream caching;
- retry policy;
- doctor mode;
- point sync by router/service.

### 17.2 Large-list support
Для `russia-inside` и подобных aggregate-списков позже потребуется:
- aggregate reporting;
- дополнительные source policy для очень больших списков;
- UX для ручного аудита shard-групп.

### 17.3 Notification layer
Отдельным модулем можно позже добавить:
- Telegram notify;
- OpenClaw proactive status;
- webhook.

Это не должно влиять на core sync engine.

---

## 18. Recommended MVP Boundaries

Чтобы не разнести проект вширь, в MVP стоит жёстко держать границы:
- только Keenetic;
- только service lists;
- только JSON config;
- только CLI;
- только RCI/KeenDNS transport;
- только sequential processing;
- без UI;
- без notifications;
- без динамического runtime-сервиса поверх batch job.

Это даст быстрый путь к работающему инструменту.

---

## 19. Summary

FQDN-updater должен быть построен как **маленький, строгий, инфраструктурный batch-инструмент**:
- запускается по расписанию;
- читает конфиг;
- получает upstream service lists;
- через RCI API по KeenDNS (и при необходимости через SSH fallback) синхронизирует managed FQDN object-group на Keenetic;
- пишет понятный лог и JSON-артефакты;
- не делает лишней магии;
- удобно управляется OpenClaw.

Если не раздувать v1, то архитектура выглядит очень жизнеспособной: она достаточно простая для быстрой реализации, но уже не кустарная и не одноразовая.

---

*This architecture is intentionally optimized for MVP delivery, operational clarity, and future extension without introducing early unnecessary complexity.*
complexity.*
ntroducing early unnecessary complexity.*
complexity.*
