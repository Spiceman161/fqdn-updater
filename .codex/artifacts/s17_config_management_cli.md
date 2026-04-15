## Orchestration Brief

Подготовить минимальный workflow-ready slice `S17: Config Management CLI` как следующий шаг после `S16`, чтобы оператор мог управлять `routers` и `mappings` через CLI без ручного редактирования JSON. Slice должен расширить только config-management UX и persistence path, не затрагивая `dry-run`, `sync`, `status`, RCI transport, artifacts/logging или packaging.

## Slice Selection

Выбран slice `S17` из `.codex/slices.json`.

Причины выбора:
- это следующий `pending` slice после завершенного `S16`;
- slice уже определен в canonical roadmap и acceptance для него узкое;
- он закрывает явный PRD/ARCHITECTURE gap по CLI-first config management без расширения product scope.

Минимальный scope slice:
- добавить CLI-группу `router` с командами `add` и `list`;
- добавить CLI-группу `mapping` с командами `set` и `list`;
- добавить overwrite-path в config persistence для безопасной атомарной записи существующего конфига;
- сохранить существующую schema-driven validation и deterministic/actionable error formatting.

Явно вне scope:
- `router update/remove`;
- `mapping remove`;
- любые `service` management команды;
- любые изменения sync/apply/dry-run/status behavior;
- любые transport, RCI, source loading, logging, artifact, Docker или systemd изменения.

## Constraints

- Соблюдать layering: CLI только парсинг аргументов и rendering, mutation orchestration в `application`, JSON persistence в `infrastructure`, validation contracts в `domain`.
- Не дублировать бизнес-валидацию в CLI; финальная истина для mutation-команд — `AppConfig.model_validate(...)`.
- `ConfigRepository` должен получить overwrite-метод с тем же atomic temp-file + replace flow, что и initial write path; atomic write logic не размазывать по CLI или application.
- Mutation flow должен быть строго `load -> transform typed config -> full validation -> atomic overwrite`.
- `router add` должен быть create-only: конфликт по `router.id` должен приводить к deterministic validation/runtime error и не менять файл.
- `mapping set` должен быть upsert по `(router_id, service_key)`: существующий mapping заменяется in-place, новый append-ится в конец.
- Existing ordering must be preserved: существующие `routers` и `mappings` не пересортировывать.
- `mapping set` должен опираться на существующие `services`; service-definition editing в `S17` не добавляется.
- `router list` и `mapping list` должны поддерживать `--output human|json` через существующий `OutputMode`.
- User-facing validation errors должны оставаться actionable и deterministic, в том числе для duplicate router id, unknown router/service references и invalid route combinations.
- Нельзя вносить scope creep в `sync`, `status`, `transport`, `packaging`, `docs/ROADMAP.md` или `.codex/slices.json` на этапе подготовки slice artifact.

## Repo Reality Check

- Текущий CLI уже экспонирует `init`, `config validate`, `dry-run`, `sync`, `status` в [`src/fqdn_updater/cli/app.py`](/home/moltuser/clawd/artifacts/fqdn-updater/src/fqdn_updater/cli/app.py:24), но command groups `router` и `mapping` отсутствуют.
- Конфиг-персистенс сейчас умеет только `load()` и `write_new()` в [`src/fqdn_updater/infrastructure/config_repository.py`](/home/moltuser/clawd/artifacts/fqdn-updater/src/fqdn_updater/infrastructure/config_repository.py:12), то есть безопасного overwrite existing config path пока нет.
- Typed domain schema уже содержит нужные модели `RouterConfig`, `RouterServiceMappingConfig`, relationship validation и deterministic duplicate/reference checks в [`src/fqdn_updater/domain/config_schema.py`](/home/moltuser/clawd/artifacts/fqdn-updater/src/fqdn_updater/domain/config_schema.py:35).
- `ConfigBootstrapService` и `ConfigValidationService` уже задают правильную границу для config workflows, но mutation-oriented application service еще отсутствует.
- Тестовая база уже покрывает CLI behavior и config repository, значит `S17` должен естественно расширить `tests/cli/` и `tests/infrastructure/`, а не заводить новый нестандартный test path.
- Canonical slice state подтверждает, что `S17` acceptance уже зафиксирован: router add/list, mapping set or equivalent, atomic config writes, actionable deterministic validation errors.

## Plan Gate Input

- Цель planning pass: зафиксировать decision-complete implementation contract для `S17` без расширения за пределы acceptance.
- Минимальный scope:
  - `fqdn-updater router add`
  - `fqdn-updater router list`
  - `fqdn-updater mapping set`
  - `fqdn-updater mapping list`
  - atomic overwrite existing config через infrastructure repository
- CLI contract для обсуждения и последующей фиксации:
  - `router add --config PATH --id ID --name NAME --rci-url URL --username USERNAME [--password-env ENV | --password-file FILE] [--enabled/--disabled] [--tag TAG ...] [--timeout-seconds N] [--allowed-source-ip CIDR ...]`
  - `router list --config PATH [--output human|json]`
  - `mapping set --config PATH --router-id ROUTER_ID --service-key SERVICE_KEY --object-group-name GROUP --route-target-type interface|gateway --route-target-value VALUE [--route-interface IFACE] [--auto/--no-auto] [--exclusive/--no-exclusive]`
  - `mapping list --config PATH [--output human|json]`
- Правила поведения, которые planning pass должен явно зафиксировать:
  - `router add` не делает upsert;
  - `mapping set` делает upsert по `(router_id, service_key)`;
  - list-команды читают уже валидированный config и рендерят deterministic output в stored order;
  - mutation-команды пишут только краткий success output в human mode;
  - все ошибки mutation path должны проходить через существующий runtime error plumbing CLI.
- Likely touched areas:
  - `src/fqdn_updater/cli/app.py`
  - новый application service для config mutation/listing orchestration
  - `src/fqdn_updater/infrastructure/config_repository.py`
- Implementation risks:
  - утечка config mutation логики в CLI handler вместо application service;
  - запись partial/invalid config без полной relationship validation;
  - потеря порядка при overwrite или при upsert existing mapping;
  - нестабильный human/json output, который усложнит deterministic tests;
  - случайный scope creep в `sync`, `status` или shared transport setup.
- Specialist review needs:
  - `python_worker` должен реализовать только production code в рамках зафиксированного scope;
  - `test_builder` должен добавить минимально нужные CLI/repository tests и прогнать финальную верификацию через `./scripts/verify.sh`;
  - `slice_gatekeeper` должен проверить layering, atomic overwrite path, deterministic validation errors и отсутствие scope creep.
- Planning gate must explicitly record that worker delegation is forbidden until the planning pass is complete and agreed. До завершения planning pass нельзя передавать задачу `python_worker`.

## Worker Prompt

Implement slice `S17: Config Management CLI` for FQDN-updater.

Read the current repository state before changing code. Follow the accepted slice scope literally and keep the change-set minimal.

Scope:
- add a `router` Typer group with `add` and `list` commands;
- add a `mapping` Typer group with `set` and `list` commands;
- add atomic overwrite persistence for existing config files;
- preserve existing validation behavior and error formatting.

Out of scope:
- `router update` or `router remove`;
- `mapping remove`;
- service management commands;
- any changes to `dry-run`, `sync`, `status`, source loading, RCI transport, logging, artifacts, Docker, or systemd assets.

Architecture requirements:
- keep CLI argument parsing and output rendering in `cli`;
- put config-management orchestration in `application`;
- keep JSON file read/write and atomic replace behavior in `infrastructure`;
- reuse the existing domain models and full `AppConfig` validation as the single source of truth;
- do not leak persistence details into CLI or domain;
- do not introduce HTTP, RCI, or router mutation concerns into this slice.

Behavior to implement:
- `router add` loads the config, appends one `RouterConfig`, validates the full updated `AppConfig`, and atomically overwrites the config file.
- `router add` is create-only. If the new router conflicts with validation rules, the command must fail without modifying the file.
- `router list` loads the config, validates it, and renders routers in stored order. Support `--output human|json` using the existing `OutputMode`.
- `mapping set` loads the config, upserts one `RouterServiceMappingConfig` by `(router_id, service_key)`, validates the full updated `AppConfig`, and atomically overwrites the config file.
- `mapping set` must replace an existing mapping in place if the key already exists; otherwise append the new mapping.
- `mapping list` loads the config, validates it, and renders mappings in stored order. Support `--output human|json`.
- `ConfigRepository` must gain an overwrite method for existing config files using the same atomic temp-file plus replace flow as new-file creation.
- Keep `auth_method="digest"` implicit for `router add`; do not expose non-digest auth options.
- Preserve deterministic, actionable validation errors through the existing repository formatting path.

Implementation defaults to lock:
- keep existing router and mapping order unchanged except for appending new items;
- mutation commands print concise human success messages only;
- list JSON output should serialize normalized model data matching the persisted schema fields;
- do not add hidden defaults beyond what the current domain models already provide.

Checks and definition of done:
- root help exposes `router` and `mapping`;
- list commands work in both `human` and `json` output modes;
- mutation flow is `load -> transform -> full validation -> atomic overwrite`;
- invalid mutations leave the config file unchanged;
- no existing command behavior regresses;
- the implementation is ready for `test_builder` to add or update tests and run `./scripts/verify.sh`.

Do not claim test ownership. Tests belong to `test_builder`, not the implementation worker.
