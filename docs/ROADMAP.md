# Roadmap

FQDN-updater развивается как узкий Keenetic-only CLI для безопасной синхронизации managed FQDN object-group, DNS route bindings и CIDR static routes через KeenDNS RCI.

## Готово

- Python CLI scaffold, typed config и validation.
- Source registry для `itdoginfo/allow-domains`.
- Загрузка и нормализация domain/CIDR списков.
- Domain suffix filters для производных builtin services.
- Read-before-write `dry-run` и `sync`.
- RCI client через KeenDNS HTTPS + HTTP Digest Auth.
- Managed object-group diff/apply, sharding и FQDN limit validation.
- Managed DNS route binding apply.
- Managed IPv4/IPv6 static route apply для CIDR entries.
- JSON artifacts, run logs и exit codes.
- `status` diagnostics.
- CLI-команды управления config/router/mapping/schedule.
- Rich terminal panel для основных операторских workflow.
- Docker Compose runtime и systemd timer.
- Ubuntu 22.04+ one-command installer и update wrapper.
- Документационный index и LLM context.

## Ближайшие задачи

- Улучшить panel workflows для массового обслуживания роутеров и списков.
- Расширить diagnostics hints для частых RCI/precondition ошибок.
- Добавить больше edge-case документации по Keenetic DNS-based routing и static routes.
- Улучшить release packaging и upgrade notes.

## Вне текущего scope

- Web UI.
- Уведомления.
- Поддержка не-Keenetic устройств.
- Production SSH transport.
- Долгоживущий daemon или self-hosted API поверх CLI.
