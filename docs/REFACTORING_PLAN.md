# Refactoring Plan

**Date:** 2026-05-01  
**Status:** Proposed incremental plan  
**Scope:** Maintainability refactoring without feature changes.

## Goal

Reduce change risk in the largest and most coupled parts of the project while preserving the current CLI behavior, artifacts, exit codes, config schema, and Keenetic RCI behavior.

This is not a rewrite plan. Each slice is intended to be one reviewable PR or a short series of tightly related commits. The slices are deliberately larger than single extraction tasks, but each has one clear architectural reason to exist.

## Baseline

Current structure is healthy enough to refactor incrementally:

- clear `cli`, `application`, `domain`, `infrastructure` layering;
- broad test coverage across domain, application, infrastructure, CLI, and packaging;
- local verification gate: `./scripts/verify.sh`;
- main refactoring pressure is concentrated in a few large files.

Primary hotspots:

- `src/fqdn_updater/cli/panel.py` - large `PanelController` mixing menu flow, rendering, dependency construction, router setup, runs, schedule, paths, and helper formatting.
- `src/fqdn_updater/application/dry_run_orchestration.py` and `src/fqdn_updater/application/sync_orchestration.py` - duplicated read/plan/failure flow with apply-specific differences.
- `src/fqdn_updater/infrastructure/keenetic_rci_client.py` - transport, retry, TLS diagnostics, command builders, and payload parsers live in one class.
- `src/fqdn_updater/domain/source_registry.py` - large static registry data mixed with factory logic.

## Slice Sizing

The first draft had too many small slices. It separated changes that are usually reviewed and validated together:

- panel dependency assembly and router setup flow;
- shared dry-run/sync planning and sync apply extraction;
- RCI command builders and response parsers.

This plan reduces the core sequence to five slices:

1. panel shell and router workflow;
2. panel secondary screens and formatting;
3. application run pipeline and apply stage;
4. RCI protocol commands and parsers;
5. RCI transport and diagnostics.

Source registry cleanup is listed as optional because it is useful, but not on the critical refactoring path.

## Guardrails

- Keep public CLI commands, panel text, JSON output, exit codes, and artifact schema stable unless a slice explicitly says otherwise.
- Preserve RCI-only production transport.
- Keep `domain` independent of `application`, `cli`, and `infrastructure`.
- Avoid broad renames in the same slice as behavioral changes.
- Add characterization tests before moving code only where existing tests do not protect the behavior being moved.
- Run `./scripts/verify.sh` after every slice.
- For RCI-related slices, prefer focused parser/builder tests before or during extraction.
- Record current `./scripts/verify.sh` status before starting the first slice and call out any slow or flaky tests in the PR notes.

## Slice 1 - Panel Shell and Router Workflow

**Status:** Passed.

**Purpose:** Turn `PanelController` from a large service locator plus router wizard into a smaller interactive shell with injected collaborators.

**Why this is one slice:**

Panel dependency construction, router add/edit/toggle flows, route target discovery, password generation, and mapping drafts are tightly coupled today. Splitting only the dependency container first creates churn without much standalone value. Moving them together gives one coherent boundary: router management.

**Changes:**

- Introduce a small panel dependency/container module, for example `src/fqdn_updater/cli/panel_dependencies.py`.
- Move service construction out of `PanelController.__init__`.
- Extract add/edit/toggle router workflows into a router-focused module, for example `src/fqdn_updater/cli/panel_router_flow.py`.
- Keep prompt adapter and console abstractions unchanged.
- Keep save behavior, secrets env writes, and rollback behavior identical.
- Move router-specific draft helpers near the new flow when they are not used elsewhere.
- Keep `PanelController` public usage from `cli/app.py` unchanged.

**Target files:**

- `src/fqdn_updater/cli/panel.py`
- `src/fqdn_updater/cli/panel_dependencies.py`
- `src/fqdn_updater/cli/panel_router_flow.py`
- `tests/cli/test_panel_controller.py`
- `tests/cli/test_panel_run_add_router_return.py`
- `tests/cli/test_panel_route_target_discovery.py`
- `tests/cli/test_app.py`

**Risk:** Medium. This touches config persistence, secrets file writes, route discovery, and test setup.

**Expected benefit:**

- Smaller `PanelController`.
- Less private attribute patching in panel tests.
- Router setup becomes easier to extend and debug.
- Dependency changes get one clear place.

**Done when:**

- Add-router, edit-router, toggle-router, route-target discovery, and rollback tests remain green.
- Existing panel entrypoint behavior is unchanged.
- Manual scan confirms no visible panel wording changed unintentionally.
- `./scripts/verify.sh` passes.

## Slice 2 - Panel Secondary Screens and Formatting

**Status:** Passed.

**Purpose:** Move lower-risk panel screens and pure formatting out of the controller after the router workflow boundary is established.

**Why this is one slice:**

Run history, schedule screens, status/dry-run/sync result rendering, and formatting helpers are all presentation concerns. They are easier to review together after the main router workflow is already separated.

**Changes:**

- Extract run history menu, run list rendering, and run detail formatting.
- Extract schedule menu and schedule rendering.
- Move pure panel formatting helpers into focused modules, for example:
  - `src/fqdn_updater/cli/panel_runs.py`;
  - `src/fqdn_updater/cli/panel_schedule.py`;
  - `src/fqdn_updater/cli/panel_formatting.py`.
- Leave `PanelController` responsible for top-level navigation and delegation.
- Keep terminal output stable unless a change is intentional and covered by tests.

**Target files:**

- `src/fqdn_updater/cli/panel.py`
- `src/fqdn_updater/cli/panel_runs.py`
- `src/fqdn_updater/cli/panel_schedule.py`
- `src/fqdn_updater/cli/panel_formatting.py`
- `tests/cli/test_panel_runs.py`
- `tests/cli/test_panel_controller.py`
- `tests/cli/test_panel_prompts.py`, only if prompt rendering helpers move.

**Risk:** Low to medium. Mostly presentation and flow wiring.

**Expected benefit:**

- `PanelController` becomes a coordinator instead of a mixed UI object.
- Formatting behavior becomes easier to test directly.
- Future panel wording/layout changes become smaller.

**Done when:**

- Existing run history, schedule, manual run, and result rendering tests pass.
- `PanelController` no longer owns most pure formatting helpers.
- `./scripts/verify.sh` passes.

## Slice 3 - Application Run Pipeline and Apply Stage

**Status:** Passed.

**Purpose:** Remove dry-run/sync drift by sharing the read/plan flow, while keeping sync writes explicit and separately testable.

**Why this is one slice:**

The duplicated dry-run/sync flow and sync apply logic are two sides of the same orchestration boundary. Extracting shared planning without shaping the apply boundary leaves sync orchestration half-refactored. Doing both together gives a clearer application structure: shared read/plan pipeline plus apply stage.

**Changes:**

- Extract shared router read/plan flow from dry-run and sync orchestration into a module such as `src/fqdn_updater/application/run_planning.py`.
- Represent shared intermediate data with explicit small models, not loose dictionaries.
- Keep dry-run and sync result models unchanged.
- Keep artifact schema and exit-code behavior unchanged.
- Extract sync write behavior into a dedicated apply service, for example `src/fqdn_updater/application/service_plan_apply.py`.
- Preserve current write order:
  - ensure object-group;
  - remove stale entries;
  - add desired entries;
  - update route binding;
  - remove static routes;
  - add static routes;
  - remove object-group when planned.
- Preserve `save_config` behavior: save once per router after successful changed services.

**Target files:**

- `src/fqdn_updater/application/dry_run_orchestration.py`
- `src/fqdn_updater/application/sync_orchestration.py`
- `src/fqdn_updater/application/run_planning.py`
- `src/fqdn_updater/application/service_plan_apply.py`
- `tests/application/test_dry_run_orchestration.py`
- `tests/application/test_sync_orchestration.py`
- optional focused tests for the apply service.

**Risk:** Medium to high. This is core behavior and failure isolation logic.

**Expected benefit:**

- Dry-run and sync stop drifting apart.
- New read/plan behavior can be added once.
- Write ordering becomes easier to review.
- Sync orchestrator becomes smaller without hiding apply semantics.

**Done when:**

- Dry-run and sync tests cover the same source failure, router failure, mapping failure, transport failure, and static-route cases.
- Existing write-order and save-after-change behavior is unchanged.
- Artifact contents remain equivalent for existing scenarios.
- `./scripts/verify.sh` passes.

## Slice 4 - RCI Protocol Commands and Parsers

**Status:** Passed.

**Purpose:** Separate Keenetic RCI protocol semantics from HTTP transport.

**Why this is one slice:**

Command builders and response parsers are both protocol-shape concerns. They should be reviewed together because they define the contract between domain/application models and Keenetic RCI JSON payloads. Transport is intentionally left for the next slice because it carries different operational risk.

**Changes:**

- Move command-building helpers into a focused module, for example `src/fqdn_updater/infrastructure/keenetic_rci_commands.py`.
- Keep exact command payloads equivalent at the Python object level.
- Move object-group, route binding, static route, DNS proxy, and interface parsers into parser module(s), for example:
  - `src/fqdn_updater/infrastructure/keenetic_rci_parsers.py`;
  - `src/fqdn_updater/infrastructure/keenetic_rci_errors.py`, only if shared error formatting needs a home.
- Prefer pure functions or small parser classes with no network state.
- Keep parser error messages stable where tests already assert them.
- Keep `KeeneticRciClient` as the coordinator that posts commands and delegates protocol details.

**Target files:**

- `src/fqdn_updater/infrastructure/keenetic_rci_client.py`
- `src/fqdn_updater/infrastructure/keenetic_rci_commands.py`
- `src/fqdn_updater/infrastructure/keenetic_rci_parsers.py`
- `tests/infrastructure/test_keenetic_rci_client.py`
- optional focused tests:
  - `tests/infrastructure/test_keenetic_rci_commands.py`;
  - `tests/infrastructure/test_keenetic_rci_parsers.py`.

**Risk:** Medium to high. Incorrect command payloads or parser drift can affect real routers.

**Expected benefit:**

- RCI client becomes much easier to read.
- Command behavior can be tested without fake HTTP responses.
- Firmware response-shape compatibility becomes easier to extend.
- Transport tests and protocol tests can be separated.

**Done when:**

- Builder tests cover object-group, route binding, static route, and save-config commands.
- Parser tests preserve object-group, route binding, static route, DNS proxy, and route-target discovery behavior.
- Existing RCI client tests pass.
- No parser rewrite is mixed with extraction.
- `./scripts/verify.sh` passes.

## Slice 5 - RCI Transport and Diagnostics

**Purpose:** Isolate request/retry/TLS diagnostic behavior after protocol command/parsing code is already out of the way.

**Why this remains its own slice:**

Transport failures are operationally important and hard to reproduce. Combining transport extraction with parser or command movement would make regressions harder to locate.

**Changes:**

- Extract HTTP opener usage, auth setup, retry loop, transport error formatting, and TLS diagnostics into a transport helper, for example `src/fqdn_updater/infrastructure/keenetic_rci_transport.py`.
- Keep timeout behavior unchanged.
- Keep retry count, delay, jitter policy, and HTTP auth semantics unchanged unless a separate product decision says otherwise.
- Keep `KeeneticRciClientFactory` public behavior unchanged.
- Keep failure messages stable enough for current diagnostics and failure classification.

**Target files:**

- `src/fqdn_updater/infrastructure/keenetic_rci_client.py`
- `src/fqdn_updater/infrastructure/keenetic_rci_transport.py`
- `tests/infrastructure/test_keenetic_rci_client.py`
- optional focused transport tests.

**Risk:** High compared with other slices.

**Expected benefit:**

- Clear boundary between network errors and RCI payload semantics.
- Better testability for retry and TLS diagnostics.
- Smaller, more maintainable RCI client.

**Done when:**

- Retry, auth failure, HTTP failure, timeout, and TLS diagnostic tests pass.
- Error strings used by diagnostics and failure classification remain stable enough for current tests.
- `KeeneticRciClient` has no direct socket/TLS retry complexity except through the transport helper.
- `./scripts/verify.sh` passes.

## Optional Cleanup - Source Registry Data

**Purpose:** Reduce noise in `domain/source_registry.py`.

This is intentionally not part of the core slice sequence. It is useful, but it does not unblock the panel, application, or RCI refactoring work.

**Changes:**

- Move large static filter lists to a dedicated data module, for example `src/fqdn_updater/domain/source_registry_data.py`.
- Keep `builtin_service_definitions()` output exactly equivalent.
- Do not introduce external runtime data files unless packaging tests are updated intentionally.

**Target files:**

- `src/fqdn_updater/domain/source_registry.py`
- `src/fqdn_updater/domain/source_registry_data.py`
- `tests/domain/test_source_registry.py`
- `tests/packaging/test_packaging_assets.py`

**Risk:** Low to medium. Main risk is packaging or accidental registry drift.

**Done when:**

- Tests assert the same service keys and representative source definitions.
- Packaging tests pass.
- `./scripts/verify.sh` passes.

## Recommended Order

1. Slice 1 - Panel shell and router workflow.
2. Slice 2 - Panel secondary screens and formatting.
3. Slice 3 - Application run pipeline and apply stage.
4. Slice 4 - RCI protocol commands and parsers.
5. Slice 5 - RCI transport and diagnostics.

Run the optional source registry cleanup when registry churn is already expected, or when a small low-risk cleanup PR is useful between higher-risk slices.

Panel slices go first because they reduce day-to-day UI change friction and have strong tests. Application orchestration comes next because it removes dry-run/sync drift before deeper infrastructure work. RCI transport stays last because it has the highest operational risk.

## Stop Conditions

Pause refactoring and reassess if any slice causes one of these:

- artifact schema changes without explicit product reason;
- CLI or panel behavior changes that are not covered by updated tests;
- failure categorization becomes less specific;
- RCI error messages lose operational diagnostics;
- test setup requires substantially more private monkeypatching;
- `./scripts/verify.sh` becomes flaky or too slow to run routinely.

## Success Criteria

The refactoring is successful when:

- largest classes are reduced to focused responsibilities;
- dry-run and sync share the same read/plan behavior;
- sync write ordering is independently testable;
- RCI client delegates command building, parsing, and transport concerns;
- tests remain at least as specific as before;
- normal development can continue slice by slice without a long-lived rewrite branch.
