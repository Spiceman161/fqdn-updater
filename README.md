# FQDN-updater

CLI-инструмент для централизованного обновления managed FQDN object-group на нескольких Keenetic-роутерах через KeenDNS RCI API.

## Workflow for Codex

В репозитории подготовлен минимальный codex workflow:

- `AGENTS.md` — правила репозитория и lifecycle для Codex
- `.codex/slices.json` — canonical slice-state для roadmap-driven работы
- `docs/ROADMAP.md` — человекочитаемое зеркало backlog и статусов
- `.codex/hooks.json` — напоминания о workflow gate
- `scripts/tmux-start-codex.sh` — быстро поднять tmux-сессию сразу в `codex`
- `scripts/tmux-codex.sh` — подготовить tmux-сессию в корне проекта
- `.codex/agents/` — роли для slice-based workflow

## Project scaffold

Репозиторий инициализирован как Python CLI-проект со `src`-layout и базовыми командами:

```bash
python -m fqdn_updater --help
fqdn-updater init --config config.json
fqdn-updater config validate --config config.json
fqdn-updater dry-run --config config.json
fqdn-updater sync --config config.json
```

`sync` в текущем состоянии применяет только managed object-group create/add/remove через RCI. Route bindings остаются вне scope до следующего slice.

## Verification

Единый локальный и CI verification entrypoint:

```bash
./scripts/verify.sh
```

Правила workflow:

- внутри workspace допустимы прямые недеструктивные правки без отдельного подтверждения;
- sandbox не считается отключённым; network/escalation по-прежнему требуют approve, если среда этого требует;
- slice не считается green, пока не прошёл `./scripts/verify.sh`.

## Quick start

```bash
cd /home/moltuser/clawd/artifacts/fqdn-updater
chmod +x scripts/tmux-start-codex.sh scripts/tmux-codex.sh
./scripts/tmux-start-codex.sh
```

Или без tmux:

```bash
cd /home/moltuser/clawd/artifacts/fqdn-updater
codex
```
