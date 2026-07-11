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
- удаляет маршрутизатор из `config.json` вместе с его mappings после подтверждения;
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

Перед discovery панель проверяет TLS/SAN endpoint на всех разрешённых адресах с SNI. Для проблемного `rci.*` имени она может, только после явного подтверждения, отправить ACME `get` и `save`; выпуск не pollится автоматически. Оператор может проверить `acme list` и внешний SAN, отложить добавление или вернуться назад. Discovery продолжается только после валидного SAN. После panel `status` такой же ремонт предлагается только для проблемных `rci.*` endpoint.

После проверки RCI URL панель показывает интерфейсы из `show interface`: тип, состояние, `global/defaultgw/priority`. Затем выбирается default interface. Если discovery недоступен, интерфейс можно ввести вручную и выбрать режим списков.

Если default interface похож на VPN, панель предлагает direct-группы (`direct_*`) и отдельно спрашивает provider interface для этих групп. Если default interface не VPN, используется прежний сценарий: выбираются VPN-routed списки и общий route target, с optional override для `google_ai`.

## Удаление роутера

Откройте `📡 Маршрутизаторы` -> `🗑 Удалить маршрутизатор`.

Панель покажет summary и попросит подтверждение. После подтверждения из `config.json`
удаляются запись маршрутизатора и связанные mappings. `.env.secrets` не очищается
автоматически.

## Списки и маршруты

Экран выбора списков показывает сервис, количество доменов, IPv4 и IPv6. Счётчики берутся из live load или cache `service-count-cache.json`.

Выбранные сервисы получают deterministic managed group names. Domain entries шардируются по FQDN object-groups, CIDR entries планируются как static routes.

Interface discovery читает интерфейсы с выбранного роутера через RCI. Сначала выбирается managed default route, затем набор списков: direct-группы для VPN default или обычный каталог для provider default. Для `google_ai` можно задать отдельный override, если AI-сервисы Google должны идти через другой интерфейс или gateway.

Если снять отметку с ранее выбранного managed списка, панель оставит его mapping в
`config.json` с `enabled=false`. Следующий `sync` прочитает текущее состояние
Keenetic и удалит только связанные с этим mapping managed object-groups, DNS
route bindings и static routes с prefix `fqdn-updater:<service>`. После успешной
cleanup mapping удаляется из `config.json`.

## Журнал и проверки

Раздел `📜 Журнал` показывает последние runs, статус, количество изменений и ошибок. В деталях видны artifact path, log path, router/service results и failure details.
Выключенные маршрутизаторы в новых run artifacts отображаются как `skipped`, без
ошибки и без сетевых запросов к Keenetic.

Главный экран показывает время начала последнего run, журнал показывает начало и
завершение run; оба экрана используют `runtime.schedule.timezone`. JSON artifacts
продолжают хранить timestamps в UTC.

`dry-run` не пишет на роутер. `sync` применяет только managed changes, включая managed default route priority diff, и сохраняет конфигурацию Keenetic после успешного apply.

## Расписание

Раздел `⏱ Расписание` редактирует `runtime.schedule` в config:

- daily или weekly mode;
- one or more `HH:MM` times;
- IANA timezone;
- systemd unit name, deployment root и Docker Compose service.

Команда `⚙ Установить/обновить в systemd` вызывает тот же installer flow, что и `fqdn-updater schedule install`: пишет host-level service/timer, делает `systemctl daemon-reload` и синхронизирует состояние timer.
