# CLI reference

Показать справку:

```bash
fqdn-updater --help
```

После установки host wrapper работает так:

- без аргументов открывает `panel`;
- `sync`, `dry-run` и `status` запускает через Docker Compose;
- `panel`, `init`, `config`, `router`, `mapping`, `schedule` запускает через локальный Python venv.

В dev checkout используйте `.venv/bin/fqdn-updater`.

## Основные команды

```bash
fqdn-updater init --config config.json
fqdn-updater panel --config config.json
fqdn-updater config validate --config config.json
fqdn-updater status --config config.json
fqdn-updater dry-run --config config.json
fqdn-updater sync --config config.json
```

- `init` создаёт стартовый config.
- `panel` открывает интерактивную терминальную панель.
- `config validate` проверяет локальный config.
- `status` выполняет read-only remote diagnostics.
- `dry-run` строит read-only sync preview.
- `sync` применяет managed changes.

`status`, `dry-run` и `sync` принимают `--output human|json`. `dry-run` и `sync` также принимают `--trigger manual|scheduled|openclaw`.

## Routers

```bash
fqdn-updater router add \
  --config config.json \
  --id home \
  --name "Home Keenetic" \
  --rci-url https://rci.example.keenetic.pro/rci/ \
  --username api_updater \
  --password-env FQDN_UPDATER_ROUTER_HOME_PASSWORD \
  --tag prod \
  --timeout-seconds 30 \
  --allowed-source-ip 203.0.113.10/32
```

Options:

- `--id`, `--name`, `--rci-url`, `--username` обязательны.
- Secret source: `--password-env` или `--password-file`.
- `--enabled/--disabled` управляет участием router в run.
- `--tag` и `--allowed-source-ip` можно повторять.

Просмотр:

```bash
fqdn-updater router list --config config.json
fqdn-updater router list --config config.json --output json
```

## Mappings

```bash
fqdn-updater mapping set \
  --config config.json \
  --router-id home \
  --service-key telegram \
  --object-group-name svc-telegram \
  --route-target-type interface \
  --route-target-value Wireguard0 \
  --auto \
  --exclusive
```

Gateway target:

```bash
fqdn-updater mapping set \
  --config config.json \
  --router-id home \
  --service-key telegram \
  --object-group-name svc-telegram \
  --route-target-type gateway \
  --route-target-value 10.2.0.1 \
  --route-interface Wireguard0
```

Просмотр:

```bash
fqdn-updater mapping list --config config.json
fqdn-updater mapping list --config config.json --output json
```

`mapping set` создаёт или заменяет mapping для пары `router_id` + `service_key` и всегда сохраняет его как `managed=true`.

## Schedule

```bash
fqdn-updater schedule show --config config.json
fqdn-updater schedule show --config config.json --output json

fqdn-updater schedule set-daily \
  --config config.json \
  --time 03:15 \
  --time 12:00 \
  --timezone Europe/Moscow

fqdn-updater schedule set-weekly \
  --config config.json \
  --day mon \
  --day thu \
  --time 03:15 \
  --timezone Europe/Moscow

fqdn-updater schedule disable --config config.json
sudo fqdn-updater schedule install --config config.json
```

`schedule install` пишет systemd unit/timer в `/etc/systemd/system`, делает `daemon-reload` и включает, перезапускает или выключает timer в зависимости от `runtime.schedule.mode`.

## Exit codes

- `0` — success without changes.
- `10` — `sync` успешно применил или обнаружил changes in plan.
- `20` — partial/failed router, service или unhealthy status.
- `30` — `dry-run` нашёл changes.
- `40` — invalid config или fatal startup error.
- `50` — lock/concurrency issue.

Typer validation errors могут возвращать собственные коды Typer до входа в use case.
