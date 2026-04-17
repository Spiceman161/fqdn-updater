# PRD: FQDN-updater
**Version:** 1.2  
**Date:** 2026-04-06  
**Status:** Draft

---

## 1. Overview
FQDN-updater — это CLI-инструмент для централизованного обновления FQDN-списков на роутерах Keenetic с удалённого VPS. Поскольку роутеры находятся за серыми IP, проект должен использовать **только RCI API через KeenDNS HTTP Proxy по HTTPS** с **HTTP Digest Authentication** и отдельным минимально привилегированным пользователем (`http-proxy` tag). SSH/CLI в MVP и базовой архитектуре не поддерживаются и не должны закладываться как эксплуатационный путь.

## 2. Goals and Success Metrics
- Primary goal: автоматизировать безопасное и повторяемое обновление FQDN object-group списков на нескольких Keenetic-роутерах из одного VPS.
- Key metrics:
  - не менее 95% scheduled run завершаются без ошибок инфраструктуры;
  - обновление всех доступных роутеров в одном прогоне выполняется без ручного вмешательства;
  - при отсутствии изменений updater не делает лишних записей на роутеры;
  - по итогам каждого прогона есть машиночитаемый статус по каждому роутеру и каждой группе;
  - оператор может выполнить `dry-run` и увидеть diff до применения изменений.

## 3. Target Users
Основной пользователь — технически уверенный администратор, управляющий несколькими Keenetic-роутерами, VPN-маршрутизацией и DNS-based routes. Его ключевая боль — поддержание сервисных списков в актуальном состоянии вручную на множестве устройств, при том что Keenetic не даёт встроенного механизма подписки или автообновления таких списков.

## 4. Scope

### In Scope (v1.0)
- CLI-инструмент для запуска sync вручную и по расписанию.
- Работа в Docker-контейнере.
- Хранение конфигурации в JSON.
- Управление конфигом через CLI-команды.
- Загрузка доменных списков из `itdoginfo/allow-domains`.
- Основной способ удалённого доступа к роутеру: `https://rci.{name}.keenetic.pro/rci/` через KeenDNS HTTP Proxy.
- В настройке KeenDNS web application для RCI upstream должен использоваться `HTTP` к порту `79`;
  внешний endpoint для updater при этом остаётся `https://rci.{name}.keenetic.pro/rci/`.
- Аутентификация к RCI API через HTTP Digest Auth.
- Поддержка выделенного API-пользователя с минимальными правами (`http-proxy` tag, без admin rights).
- Опциональная рекомендация по firewall whitelist доступа к KeenDNS-публикации только с IP VPS.
- Поддержка сервисных списков:
  - News
  - HDRezka
  - Meta*
  - Tik-Tok
  - Twitter
  - YouTube
  - Discord
  - Cloudflare
  - Telegram
  - Google Meet
  - Google AI
- Diff-sync для Keenetic object-group:
  - добавить новые записи;
  - удалить исчезнувшие записи;
  - не трогать неизменённые.
- Поддержка object-group, содержащих как FQDN, так и CIDR-подсети, если сервисный сценарий этого требует.
- Работа с несколькими роутерами за один прогон.
- Продолжение обработки остальных роутеров при ошибке на одном устройстве.
- Отдельный журнал работы и JSON-артефакты состояния/результатов.
- `dry-run`, `validate-config`, `sync`, `status`.
- OpenClaw-friendly эксплуатация: предсказуемые команды, читаемые логи, машиночитаемый output.

### Out of Scope (future versions)
- Web UI / dashboard.
- Система уведомлений (Telegram/email/webhook).
- Поддержка не-Keenetic устройств.
- Self-hosted API-сервис поверх CLI.
- Автоматическое обнаружение роутеров в сети.
- Полноценная multi-user RBAC модель.
- Поддержка SSH как transport-слоя.

## 5. Core Features and Functional Requirements

### 5.1 Source Fetching and Normalization
**Description:** Инструмент скачивает upstream-списки доменов из `itdoginfo/allow-domains`, нормализует их и готовит к сравнению с текущим состоянием на роутере.  
**User story:** Как администратор, я хочу автоматически получать свежие доменные списки из одного доверенного источника, чтобы не поддерживать их вручную.  
**Acceptance criteria:**
- [ ] Для каждого поддерживаемого сервиса updater умеет загрузить соответствующий upstream raw-list по URL.
- [ ] Инструмент отфильтровывает пустые строки, комментарии и дубликаты.
- [ ] Нормализация доменов приводит записи к каноничному виду для сравнения.
- [ ] Если upstream недоступен, это фиксируется в логе и статусе, но не приводит к порче локального state.
**Technical notes:** Использовать raw `.lst` файлы из GitHub; source mapping должен быть явным в конфиге или коде. `google_ai` должен использовать отдельный список, а не общий `geoblock`.

### 5.2 Router Inventory, Access Method, and Route Mapping
**Description:** Система хранит список роутеров, метод доступа к ним и назначение сервисных групп на интерфейсы/шлюзы Keenetic.  
**User story:** Как администратор, я хочу в одном месте описать все роутеры, способ авторизации к ним и то, какие сервисы через что должны маршрутизироваться.  
**Acceptance criteria:**
- [ ] Конфиг хранится в JSON-файле.
- [ ] CLI-команды позволяют добавить/изменить/удалить роутер без ручного редактирования JSON.
- [ ] Для каждого роутера можно задать transport type (`rci` или `ssh`), endpoint/host, auth method и mapping `service -> target route/interface`.
- [ ] Для `rci`-доступа можно задать KeenDNS RCI endpoint, Digest username и ссылку на секрет пароля через env/file reference.
- [ ] Конфиг валидируется до запуска sync.
**Technical notes:** JSON можно редактировать вручную, но основным UX считаются команды-мастера вроде `router add`, `router update`, `mapping set`, `config validate`. Для внешнего VPS `rci` считается основным transport profile.

### 5.3 Keenetic State Read
**Description:** Перед изменениями updater считывает текущее состояние object-group на роутере.  
**User story:** Как администратор, я хочу, чтобы updater понимал текущее состояние роутера, а не “стрелял вслепую”.  
**Acceptance criteria:**
- [ ] Инструмент умеет подключиться к Keenetic через настроенный transport (`rci` в приоритете, `ssh` как fallback).
- [ ] Инструмент умеет получить текущий список элементов целевой object-group.
- [ ] Если object-group отсутствует, это корректно определяется и отражается как create/init path.
- [ ] Ошибки чтения не останавливают обработку остальных роутеров.
**Technical notes:**
- Для SSH-пути использовать CLI-команду `show object-group fqdn [<group>]` и при необходимости `show dns-proxy`.
- Для RCI-пути предусмотреть отдельный client adapter поверх `POST https://rci.{name}.keenetic.pro/rci/` с HTTP Digest Auth.
- Архитектура должна скрывать различия между transport-способами за единым client interface.

### 5.4 Diff-Sync Apply
**Description:** Обновление выполняется через разницу между desired state и actual state, а не через тотальную перезапись.  
**User story:** Как администратор, я хочу минимальные и безопасные изменения на роутере, чтобы не ломать рабочие правила лишними операциями.  
**Acceptance criteria:**
- [ ] Для каждой группы вычисляются `to_add`, `to_remove`, `unchanged`.
- [ ] Если изменений нет, updater не отправляет write-команды на роутер.
- [ ] Изменения применяются только к управляемым группам.
- [ ] При ошибке применения для одной группы остальные группы и роутеры продолжают обрабатываться.
- [ ] Для RCI-пути изменения отправляются батчами разумного размера; большие обновления чанкуются.
**Technical notes:**
- SSH/CLI-синтаксис:
  - Создание группы: `object-group fqdn <name>`.
  - Добавление элемента: `include <address>`.
  - Исключение элемента: `exclude <address>` при необходимости поддержки исключений в будущем.
  - Маршрутизация группы: `dns-proxy route object-group <group> (( <gateway> [ <interface> ]) | <interface> ) [ auto ] [ reject ]`.
  - Удаление маршрута: `no route [ object-group <group> ] [ <interface> ]`.
- RCI API path:
  - endpoint: `https://rci.{name}.keenetic.pro/rci/`
  - auth: HTTP Digest Auth
  - команды отправляются JSON-массивом RCI-команд
  - при больших объёмах применять batching/chunking (ориентир: 200–500 команд на POST)
  - `system configuration save` должен вызываться после успешного apply-batch, иначе изменения могут не сохраниться
- `auto` означает применение маршрута только при доступности шлюза.
- `reject` означает эксклюзивный маршрут: если интерфейс неактивен, трафик не должен уйти через fallback.
- Нужен локальный policy guard, чтобы updater изменял только object-group, помеченные как managed by FQDN-updater.

### 5.5 Dry Run and Diff Output
**Description:** Перед реальным применением оператор или OpenClaw может посмотреть ожидаемые изменения.  
**User story:** Как администратор, я хочу увидеть, что именно изменится, до того как updater полезет в боевые роутеры.  
**Acceptance criteria:**
- [ ] Команда `dry-run` не вносит изменений на роутеры.
- [ ] `dry-run` показывает diff по каждому роутеру и каждой группе.
- [ ] Есть текстовый человекочитаемый вывод и JSON output mode.
- [ ] Exit code позволяет отличить “ошибок нет, изменений нет” от “есть изменения” и “есть ошибка”.
**Technical notes:** Это ключевой OpenClaw-friendly сценарий для автоматического анализа и последующего подтверждённого запуска.

### 5.6 Logging and Run Artifacts
**Description:** Каждый прогон сохраняет структурированную информацию о ходе и результате.  
**User story:** Как администратор, я хочу потом понять, что произошло: что обновилось, где ошибка, что не изменилось.  
**Acceptance criteria:**
- [ ] Каждый run пишет отдельный лог-файл или отдельную лог-сессию.
- [ ] По итогам run создаётся JSON summary artifact.
- [ ] Для каждого роутера сохраняется статус: `updated`, `no_changes`, `failed`, `partial`.
- [ ] При ошибке фиксируются router id, service group, step, stderr/exception message и timestamp.
**Technical notes:** Логирование в stdout/stderr + запись в volume; для systemd/docker это упростит сбор через journal и чтение OpenClaw.

### 5.7 Scheduled Execution
**Description:** Прогоны выполняются по расписанию через systemd timer на VPS.  
**User story:** Как администратор, я хочу, чтобы sync шёл автоматически без постоянно живущего демона.  
**Acceptance criteria:**
- [ ] Проект поставляет пример `systemd service` и `systemd timer`.
- [ ] Таймер запускает одноразовый контейнерный job.
- [ ] При падении run это не ломает последующие scheduled запуски.
- [ ] Интервалы запуска настраиваются без изменений кода.
**Technical notes:** Предпочтительный сценарий: `systemd timer -> docker compose run --rm fqdn-updater sync`.

### 5.8 Keenetic Compatibility, Remote Access, and Preconditions
**Description:** Инструмент должен явно проверять обязательные условия, без которых DNS-based routes или удалённый доступ к роутеру не работают корректно.  
**User story:** Как администратор, я хочу заранее видеть инфраструктурные проблемы, а не разбираться потом, почему маршруты “вроде есть, но не работают” или почему VPS не может достучаться до роутера.  
**Acceptance criteria:**
- [ ] Документация проекта перечисляет обязательные условия работы DNS-based routes на Keenetic.
- [ ] Документация проекта перечисляет обязательные условия доступа через KeenDNS RCI API.
- [ ] Команда `validate-config` проверяет локально всё, что можно проверить без входа на роутер.
- [ ] Команда `status` или `doctor` в будущих версиях может использоваться для удалённой диагностики.
**Technical notes:** В PRD фиксируются обязательные runtime-предпосылки:
- DNS proxy service на Keenetic должен быть включён (`service dns-proxy`).
- Клиент должен использовать IP роутера как единственный DNS-сервер.
- Клиент должен находиться в `Default policy`.
- Целевой интерфейс маршрута должен иметь статус `Connected`.
- Для внешнего VPS по RCI должен быть опубликован KeenDNS endpoint `https://rci.{name}.keenetic.pro/rci/` как HTTP Proxy с обязательной авторизацией.
- Для этой публикации в web UI Keenetic выбирается protocol `HTTP` и порт `79`; `HTTPS` в config относится к внешнему клиентскому соединению updater -> KeenDNS endpoint.
- Для RCI рекомендуется отдельный пользователь `api_updater` с тегом `http-proxy` и без admin rights.
- Рекомендуется firewall whitelist доступа к KeenDNS HTTP Proxy только с IP VPS, если IP статический.

## 6. Technical Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| CLI app | Python 3.12+ | Быстро и надёжно подходит для SSH automation, JSON-конфига, diff logic и CLI-инструментов |
| CLI framework | Typer | Удобный UX для командного интерфейса и автодокументации |
| Validation | Pydantic v2 + pydantic-settings | Даёт строгую валидацию конфигов и предсказуемую схему данных |
| Remote API | HTTPS + requests/httpx + HTTP Digest Auth | Основной путь удалённого управления через KeenDNS RCI API с внешнего VPS |
| SSH fallback | Paramiko | Fallback-вариант для локальной сети, прямого доступа или отладки |
| State/Artifacts | JSON files | Простые машиночитаемые артефакты, удобные для OpenClaw |
| Logging | Python logging (JSON/text modes) | Позволяет иметь и человекочитаемые логи, и структурированные записи |
| Packaging | Docker / Docker Compose | Изоляция от хоста, воспроизводимость и удобный scheduled job |
| Scheduling | systemd service + timer | Прозрачная эксплуатация на VPS без отдельного daemon |
| Testing | pytest | Дешёвый и понятный baseline для unit/integration tests |
| Formatting / Lint | Ruff + Black | Минимум трения в кодовой базе |

**Assumptions:** Предполагается, что у администратора уже есть рабочий способ доступа к Keenetic-роутерам: предпочтительно опубликованный KeenDNS RCI endpoint с HTTP Digest Auth, либо SSH как fallback. Целевые object-group имена приведены к единому стандарту до запуска проекта. CLI-синтаксис работы с FQDN object-group и route object-group считается подтверждённым для SSH-ветки реализации; RCI-ветка должна использовать тот же доменный контракт через JSON-команды API.

## 7. Data Model

- **Router**
  - `id` (string)
  - `name` (string)
  - `rci_url` (string)
  - `username` (string)
  - `auth_method` (enum: digest)
  - `password_env` (string, nullable)
  - `password_file` (string, nullable)
  - `enabled` (boolean)
  - `tags` (array[string])
  - `timeout_seconds` (integer)
  - `allowed_source_ips` (array[string], optional, documentation/audit only)
  - `created_at` (timestamp, optional)

- **ServiceDefinition**
  - `key` (string, unique) — e.g. `telegram`, `youtube`, `google_ai`
  - `source_urls` (array[string]) and `format` for legacy single-format sources
  - `sources` (array[object]) for per-source mixed domain/CIDR lists
    - `url` (string)
    - `format` (enum: raw_domain_list/raw_cidr_list/mixed)
  - `enabled` (boolean)
  - `description` (string, optional)

### 8.1 Keenetic FQDN limits and sharding

Confirmed operational limits for managed `object-group fqdn` planning:

- one FQDN object-group must not contain more than 300 entries;
- the total number of entries across the router FQDN section must not exceed 1024.

The updater must automatically shard a managed service list above 300 entries into deterministic
managed object-groups. The first shard keeps the configured `object_group_name`; following shards
use `-2`, `-3`, `-4` suffixes. A managed router plan above 1024 FQDN entries must still be rejected
before any router write. If a previously needed shard becomes stale after an upstream list shrinks,
the updater must remove its entries and remove its route binding.

- **RouterServiceMapping**
  - `router_id` (string, foreign key -> Router.id)
  - `service_key` (string, foreign key -> ServiceDefinition.key)
  - `object_group_name` (string)
  - `route_target_type` (enum: interface/gateway)
  - `route_target_value` (string)
  - `route_interface` (string, nullable)
  - `exclusive` (boolean)
  - `auto` (boolean)
  - `managed` (boolean)

- **Run**
  - `run_id` (string/uuid)
  - `started_at` (timestamp)
  - `finished_at` (timestamp)
  - `trigger` (enum: manual/scheduled/openclaw)
  - `mode` (enum: dry_run/apply)
  - `status` (enum: success/partial/failed)

- **RunResult**
  - `run_id` (foreign key)
  - `router_id` (foreign key)
  - `service_key` (foreign key)
  - `status` (enum: updated/no_changes/failed/skipped)
  - `added_count` (integer)
  - `removed_count` (integer)
  - `unchanged_count` (integer)
  - `error_message` (string, nullable)

## 8. Architecture Overview
Поток данных для одного запуска выглядит так:

1. systemd timer запускает одноразовый контейнер с командой `sync`.
2. CLI загружает JSON-конфиг и валидирует его.
3. Для каждого включённого сервиса скачивается upstream raw-list.
4. Списки нормализуются и кешируются в памяти на время run.
5. Для каждого включённого роутера updater:
   - открывает transport-сессию (`rci` по HTTPS через KeenDNS либо `ssh` fallback);
   - считывает текущее состояние нужных object-group;
   - при необходимости проверяет наличие/доступность маршрута и базовую диагностическую информацию DNS proxy;
   - строит diff между desired и actual;
   - в режиме `apply` отправляет только необходимые команды изменения;
   - при необходимости создаёт/обновляет маршрут для object-group;
   - для RCI после успешного batched apply вызывает сохранение конфигурации;
   - пишет результат в лог и run summary.
6. После завершения формируется итоговый JSON-артефакт с полным статусом.

Асинхронность в v1 не обязательна. Последовательная обработка роутеров проще для отладки и безопаснее для CLI-driven инфраструктурного инструмента. Если число роутеров вырастет, можно позже добавить контролируемый parallelism.

## 9. Security Considerations
- Authentication method:
  - для внешнего VPS используется HTTP Digest Auth к KeenDNS RCI API;
  - использовать отдельного пользователя `api_updater` с тегом `http-proxy` и без admin rights.
- Authorization model: доступ на уровне владения конфигом и контейнером; внутри самого MVP отдельной RBAC-модели нет.
- Key risks:
  - компрометация Digest credentials;
  - ошибка diff/apply может повредить объектные группы;
  - компрометация upstream source или сетевой MITM;
  - случайное изменение неуправляемых групп;
  - публичная доступность KeenDNS endpoint без IP whitelist.
- Compliance notes:
  - специальных регуляторных требований типа GDPR/HIPAA для самого инструмента не ожидается;
  - конфиг и логи не должны содержать лишние секреты в открытом виде;
  - пароль RCI-пользователя должен храниться только в env/file secret и не попадать в репозиторий.

## 10. UI/UX Principles
- CLI должен быть предсказуемым: одна команда — один понятный результат.
- Любое опасное действие должно иметь `dry-run` эквивалент.
- Ошибки должны быть диагностическими, а не абстрактными.
- Машиночитаемый JSON output должен быть first-class citizen, а не вторичной опцией.
- Конфиг лучше заполнять через CLI-команды, а не заставлять пользователя помнить структуру JSON вручную.

## 11. Development Phases

### Phase 1 — MVP (target: 1–2 недели)
- [ ] Создать JSON schema/config model
- [ ] Реализовать CLI scaffold (`init`, `validate-config`, `sync`, `dry-run`, `status`)
- [ ] Реализовать загрузку и нормализацию upstream service lists
- [ ] Реализовать RCI API client с HTTP Digest Auth
- [ ] Реализовать чтение current object-group state
- [ ] Реализовать diff engine
- [ ] Реализовать apply engine для managed groups
- [ ] Реализовать create/update route binding для object-group
- [ ] Реализовать structured logging и run summary JSON
- [ ] Подготовить Dockerfile и compose example
- [ ] Подготовить systemd service/timer examples

### Phase 2 — Hardening
- [ ] Retry policy для сетевых ошибок
- [ ] Кеш upstream с TTL
- [ ] Более аккуратные exit codes
- [ ] Интеграционные тесты с mock SSH / sandbox router
- [ ] Locking для защиты от параллельных запусков
- [ ] Backup current state before apply

### Phase 3 — Extended Routing
- [ ] Поддержка aggregate-списков
- [ ] Дополнительные источники списков
- [ ] Selective notification hooks
- [ ] `doctor` режим для проверки обязательных условий на роутере

## 12. Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Реальное поведение Keenetic CLI/RCI частично зависит от версии KeeneticOS | Medium | High | Изолировать vendor-specific команды в adapter layer и проверять их на реальном роутере в integration tests |
| Один из сервисных списков upstream исчезнет или сменит путь/структуру | Medium | Medium | Держать явный source map, проверять HTTP status, не удалять state вслепую при source failure |
| Ошибка diff logic удалит валидные домены | Low | High | Обязательный `dry-run`, integration tests, режим managed-only, backup в следующей фазе |
| Параллельные или overlapping запуски повредят state/logs | Medium | Medium | Добавить file lock / run lock уже в early hardening |
| RCI endpoint через KeenDNS окажется публично доступен и станет целью брутфорса | Medium | High | Использовать отдельного low-privilege пользователя, длинный пароль, IP whitelist по VPS и аудит неудачных попыток |
| Список Cloudflare или другие большие сервисные группы окажутся слишком объёмными для одного object-group | Medium | High | Автоматически шардировать managed mapping по 300 записей на группу и отклонять план выше общего лимита 1024 записей на роутер |
| DNS-based routes на роутере настроены формально, но не работают из-за `Default policy`, стороннего DNS или выключенного `service dns-proxy` | High | High | Явно документировать обязательные условия и позднее добавить режим `doctor` для диагностики |
| Большой apply через RCI превысит безопасный размер одного POST-батча | Medium | Medium | Чанковать команды и сохранять конфигурацию после успешных apply-этапов |

## 13. Open Questions
- [ ] Нужно ли в v1 хранить snapshot текущего состояния роутера перед apply для rollback/manual audit?
- [ ] Должен ли `Cloudflare` реально идти как доменный список, если часть сценариев лучше решается через subnet/IP routing?
- [ ] Нужен ли отдельный naming convention validator для object-group имён на Keenetic?
- [ ] Нужен ли отдельный режим `sync router <id>` для точечного прогона одного устройства?
- [ ] Должен ли проект в v1 поддерживать смешанные object-group (FQDN + CIDR), что особенно полезно для Meta/WhatsApp-подобных сервисов?

## 14. Future Possibilities (v2+)
- Расширенная поддержка aggregate-списков вроде `russia-inside`
- Уведомления о неуспешных прогонах в Telegram/OpenClaw
- Автообнаружение конфигурационных drift’ов на роутерах
- История изменений по каждой группе
- Web UI / dashboard
- Поддержка нескольких upstream-источников
- Read-only health check mode для OpenClaw heartbeat

---
*Generated with PRD Creation Assistant. Validate KeeneticOS behavior on a real router and confirm object-group entry limits before implementing large lists.*
idate KeeneticOS behavior on a real router and confirm object-group entry limits before implementing large lists.*
p entry limits before implementing large lists.*
s before implementing large lists.*
p entry limits before implementing large lists.*
