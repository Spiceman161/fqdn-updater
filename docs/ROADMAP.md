# Roadmap

FQDN-updater развивается как узкий Keenetic-only CLI для безопасной синхронизации managed FQDN object-group через KeenDNS RCI.

## Готово

- Python CLI scaffold, typed config и validation.
- Source registry для `itdoginfo/allow-domains`.
- Загрузка и нормализация domain/CIDR списков.
- Read-before-write `dry-run` и `sync`.
- RCI client через KeenDNS HTTPS + HTTP Digest Auth.
- Managed object-group diff/apply и route binding apply.
- JSON artifacts, run logs и exit codes.
- `status` diagnostics.
- CLI-команды управления config/router/mapping/schedule.
- Rich terminal panel для основных операторских workflow.
- Docker Compose runtime и systemd timer.
- Ubuntu 22.04+ one-command installer.

## Ближайшие задачи

- Улучшить panel workflows для массового обслуживания роутеров и списков.
- Расширить документацию по edge cases Keenetic DNS-based routing.
- Добавить более подробные diagnostics hints для частых RCI/precondition ошибок.
- Улучшить release packaging и upgrade notes.

## Вне текущего scope

- Web UI.
- Уведомления.
- Поддержка не-Keenetic устройств.
- Production SSH transport.
- Долгоживущий daemon или self-hosted API поверх CLI.
