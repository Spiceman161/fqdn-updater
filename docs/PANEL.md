# Панель

Панель — интерактивный терминальный интерфейс для локального обслуживания `config.json`, секретов и операторских запусков.

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

## Что меняет панель

Локальные записи:

- создаёт или обновляет `config.json`;
- записывает generated password в `.env.secrets`;
- читает и показывает run history из `data/artifacts`;
- может установить systemd units только через явную команду расписания.

Удалённые read-only проверки:

- проверка связи с роутером;
- WireGuard interface discovery;
- `status`;
- `dry-run`.

Удалённая запись:

- только `sync` выбранного роутера или обычный `sync`;
- только managed object-group и route binding;
- только после read-before-write diff.

## Добавление роутера

Откройте `Маршрутизаторы` -> `Добавить новый маршрутизатор`.

Панель сгенерирует router id из имени, предложит `api_updater`, создаст пароль и покажет подсказки для Keenetic. Пароль сохраняется в `.env.secrets`, а в config остаётся ссылка вида `FQDN_UPDATER_ROUTER_<ID>_PASSWORD`.

## Списки и маршруты

Экран выбора списков показывает сервис, количество доменов, IPv4 и IPv6. Выбранные сервисы получают deterministic managed group names.

WireGuard discovery читает интерфейсы с выбранного роутера через RCI. Если discovery недоступен, можно ввести interface или gateway вручную.

Для `google_ai` можно задать отдельный override, если AI-сервисы Google должны идти через другой интерфейс.

## Журнал и проверки

Раздел `Журнал` показывает последние runs, статус, количество изменений и ошибок. Отсюда удобно выполнить `dry-run` и затем `sync` после проверки плана.

`dry-run` не пишет на роутер. `sync` применяет только managed changes и сохраняет конфигурацию Keenetic после успешного apply.

## Расписание

Раздел `Расписание` редактирует `runtime.schedule` в config и устанавливает systemd timer через `schedule install`. Сам timer запускает одноразовый Docker Compose job.
