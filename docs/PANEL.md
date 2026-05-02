# Панель

Панель — интерактивный терминальный интерфейс для локального обслуживания `config.json`, секретов, расписания и операторских запусков.

## Запуск

```bash
fqdn-updater
domaingo
fqdn-updater panel --config /opt/fqdn-updater/config.json
```

В неинтерактивном stdin панель не запускается; используйте CLI-команды.

## Навигация

- стрелки выбирают пункт;
- `Enter` открывает или подтверждает;
- `Space` отмечает checkbox;
- `Esc` возвращает назад.

Главные разделы: `📡 Маршрутизаторы`, `🧭 Списки и маршруты`, `🚀 Ручной запуск`, `⏱ Расписание`, `📜 Журнал`, `✅ Проверка конфига`, `ℹ О панели`.

Иконки в панели декоративные: они помогают быстрее отличать разделы, статусы и действия, но не меняют сценарии и клавиатурное управление.

## Что меняет панель

Локальные записи:

- создаёт или обновляет `config.json`;
- синхронизирует builtin service registry;
- записывает generated password в `.env.secrets`;
- читает и показывает run history из `data/artifacts`;
- редактирует `runtime.schedule`;
- устанавливает systemd units только после явной команды расписания.

Удалённые read-only проверки:

- проверка связи с роутером;
- WireGuard interface discovery;
- `status`;
- `dry-run`.

Удалённая запись:

- только `sync`;
- только managed object-groups, DNS route bindings и static routes;
- только после read-before-write diff.

## Добавление роутера

Откройте `📡 Маршрутизаторы` -> `➕ Добавить новый маршрутизатор`.

Панель сгенерирует router id из имени, предложит `api_updater`, создаст пароль и покажет подсказки для Keenetic. Пароль сохраняется в `.env.secrets`, а в config остаётся ссылка вида `FQDN_UPDATER_ROUTER_<ID>_PASSWORD`.

После проверки RCI URL можно сразу выбрать сервисы и route target. Если discovery недоступен, route target можно ввести вручную.

## Списки и маршруты

Экран выбора списков показывает сервис, количество доменов, IPv4 и IPv6. Счётчики берутся из live load или cache `service-count-cache.json`.

Выбранные сервисы получают deterministic managed group names. Domain entries шардируются по FQDN object-groups, CIDR entries планируются как static routes.

WireGuard discovery читает интерфейсы с выбранного роутера через RCI. Для `google_ai` можно задать отдельный override, если AI-сервисы Google должны идти через другой интерфейс или gateway.

## Журнал и проверки

Раздел `📜 Журнал` показывает последние runs, статус, количество изменений и ошибок. В деталях видны artifact path, log path, router/service results и failure details.

`dry-run` не пишет на роутер. `sync` применяет только managed changes и сохраняет конфигурацию Keenetic после успешного apply.

## Расписание

Раздел `⏱ Расписание` редактирует `runtime.schedule` в config:

- daily или weekly mode;
- one or more `HH:MM` times;
- IANA timezone;
- systemd unit name, deployment root и Docker Compose service.

Команда `⚙ Установить/обновить в systemd` вызывает тот же installer flow, что и `fqdn-updater schedule install`: пишет host-level service/timer, делает `systemctl daemon-reload` и синхронизирует состояние timer.
