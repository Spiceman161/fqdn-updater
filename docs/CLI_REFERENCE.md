# CLI reference

Показать справку:

```bash
fqdn-updater --help
```

Основные команды:

- `fqdn-updater panel --config config.json` — открыть интерактивную панель.
- `fqdn-updater init --config config.json` — создать стартовый конфиг.
- `fqdn-updater config validate --config config.json` — проверить локальный конфиг.
- `fqdn-updater router add ...` — добавить роутер.
- `fqdn-updater router list --config config.json` — показать роутеры.
- `fqdn-updater mapping set ...` — создать или заменить mapping.
- `fqdn-updater mapping list --config config.json` — показать mappings.
- `fqdn-updater status --config config.json` — read-only remote diagnostics.
- `fqdn-updater dry-run --config config.json` — read-only sync preview.
- `fqdn-updater sync --config config.json` — применить managed изменения.
- `fqdn-updater schedule show --config config.json` — показать расписание.
- `fqdn-updater schedule set-daily --time HH:MM --timezone Europe/Moscow` — daily schedule.
- `fqdn-updater schedule set-weekly --day mon --time HH:MM` — weekly schedule.
- `fqdn-updater schedule install --config config.json` — установить systemd units.

Для машинного вывода используйте `--output json` там, где опция доступна.
