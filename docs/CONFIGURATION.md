# Конфигурация

Основной файл — `config.json`. Он хранит только настройки и ссылки на секреты; реальные пароли должны лежать в `.env.secrets`, `.env` или `secrets/`.

Минимальная форма:

```json
{
  "version": 1,
  "routers": [],
  "services": [],
  "mappings": [],
  "runtime": {}
}
```

`fqdn-updater init` создаёт валидный scaffold со встроенными сервисами. Панель при запуске синхронизирует builtin service registry и сохраняет пользовательские custom services.

## Routers

Роутер описывает KeenDNS RCI endpoint, пользователя и secret source:

```json
{
  "id": "home",
  "name": "Home Keenetic",
  "rci_url": "https://rci.example.keenetic.pro/rci/",
  "username": "api_updater",
  "auth_method": "digest",
  "password_env": "FQDN_UPDATER_ROUTER_HOME_PASSWORD",
  "password_file": null,
  "enabled": true,
  "tags": ["prod"],
  "timeout_seconds": 30,
  "allowed_source_ips": ["203.0.113.10/32"]
}
```

Правила:

- `id` должен быть уникальным slug: lowercase letters, digits, `_` и `-`.
- `rci_url` должен быть `https://.../rci/`; CLI и панель нормализуют вставленный `http://rci...` в HTTPS endpoint.
- `auth_method` сейчас только `digest`.
- Enabled router должен иметь ровно один secret source: `password_env` или `password_file`.
- `allowed_source_ips` — audit/операторская подсказка для whitelist публикации; доступ на Keenetic настраивается отдельно.

## Services

Service описывает один логический список. Встроенный registry использует raw-URL из `itdoginfo/allow-domains`; сами upstream lists не вендорятся в репозиторий.

Новый формат:

```json
{
  "key": "telegram",
  "enabled": true,
  "description": "Telegram domains and subnets",
  "sources": [
    {
      "url": "https://example.invalid/telegram-domains.lst",
      "format": "raw_domain_list",
      "include_domain_suffixes": ["telegram.org"],
      "exclude_domain_suffixes": []
    },
    {
      "url": "https://example.invalid/telegram-ipv4.lst",
      "format": "raw_cidr_list"
    }
  ]
}
```

Legacy формат всё ещё валиден:

```json
{
  "key": "telegram",
  "source_urls": ["https://example.invalid/telegram.lst"],
  "format": "raw_domain_list",
  "enabled": true
}
```

Правила:

- `key` должен быть уникальным slug.
- Используйте либо `sources`, либо `source_urls` + `format`; смешивать нельзя.
- `format`: `raw_domain_list`, `raw_cidr_list` или `mixed`.
- Пустые строки, comments и дубликаты отбрасываются.
- Domain entries нормализуются и сортируются детерминированно.
- CIDR entries нормализуются как IPv4/IPv6 networks.
- `include_domain_suffixes` и `exclude_domain_suffixes` доступны только для domain sources.
- Если один source сервиса не загрузился или не распарсился, сервис помечается failed; updater не делает blind deletion по неполному source.

## Mappings

Mapping связывает router, service и managed route target:

```json
{
  "router_id": "home",
  "service_key": "telegram",
  "object_group_name": "svc-telegram",
  "route_target_type": "interface",
  "route_target_value": "Wireguard0",
  "route_interface": null,
  "exclusive": true,
  "auto": true,
  "managed": true
}
```

Что делает `sync`:

- для domain entries создаёт или обновляет managed `object-group fqdn`;
- для FQDN object-groups создаёт DNS-proxy route binding на `route_target_value`;
- для CIDR entries создаёт managed IPv4/IPv6 static routes с comment prefix `fqdn-updater:<service>`;
- меняет только mappings с `managed=true`;
- не трогает unrelated object-groups, DNS routes и static routes.

Route target:

- `route_target_type=interface` — `route_target_value` это interface, `route_interface` должен быть unset;
- `route_target_type=gateway` — `route_target_value` это gateway, `route_interface` можно задать дополнительно;
- `auto` и `exclusive` пишутся явно.

FQDN limits:

- до 300 FQDN entries в одном object-group;
- до 1024 managed FQDN entries суммарно на роутер;
- списки выше 300 entries шардируются в deterministic names: `name`, `name-2`, `name-3`, ...

## Runtime

```json
{
  "runtime": {
    "artifacts_dir": "data/artifacts",
    "logs_dir": "data/logs",
    "state_dir": "data/state",
    "secrets_env_file": ".env.secrets",
    "log_format": "text",
    "schedule": {
      "mode": "daily",
      "times": ["03:15"],
      "weekdays": [],
      "timezone": "Europe/Moscow",
      "systemd": {
        "unit_name": "fqdn-updater",
        "deployment_root": "/opt/fqdn-updater",
        "compose_service": "fqdn-updater"
      }
    }
  }
}
```

Runtime paths are resolved relative to the directory containing `config.json`, unless the path is absolute.

- `artifacts_dir` — JSON run artifacts.
- `logs_dir` — text/JSON run logs.
- `state_dir` — локальное state и cache.
- `secrets_env_file` — env-файл, который загружается перед `status`, `dry-run` и `sync`.
- `log_format` — `text` или `json`.

Кэш счётчиков сервисов хранится рядом с artifacts parent как `service-count-cache.json`; он нужен панели для быстрых счётчиков domain/IPv4/IPv6.

## Schedule

`runtime.schedule.mode`:

- `disabled` — timer выключен, `times` и `weekdays` пустые;
- `daily` — нужны one or more `times`;
- `weekly` — нужны one or more `times` и `weekdays`.

`times` задаются в `HH:MM`, `timezone` — валидная IANA timezone. `schedule install` рендерит host-level systemd service/timer и запускает Docker Compose job:

```text
docker compose run --rm fqdn-updater sync --trigger scheduled --config /work/config.json
```
