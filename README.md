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
fqdn-updater status --config config.json
```

Для VPS deployment есть минимальный Docker Compose runtime и примеры systemd unit/timer.
Операторский сценарий описан в `docs/USER_QUICKSTART.md`.

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

## Keenetic FQDN limits

Перед `dry-run`/`sync` план проверяется по подтверждённым ограничениям Keenetic:

- не больше 300 записей в одном `object-group fqdn`;
- не больше 1024 записей суммарно в управляемом FQDN-плане роутера.

Если список для одного mapping содержит больше 300 записей, updater автоматически делит его на
managed shard-группы: базовая группа сохраняет имя из `object_group_name`, следующие получают
суффиксы `-2`, `-3`, `-4`. Лишние shard-группы после уменьшения upstream-списка очищаются, а их
route binding удаляется.
