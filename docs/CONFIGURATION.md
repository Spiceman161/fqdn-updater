# Конфигурация

Основной файл — `config.json`.

## Routers

Роутер описывает KeenDNS RCI endpoint, пользователя и ссылку на секрет:

```json
{
  "id": "home",
  "name": "Home Keenetic",
  "rci_url": "https://rci.example.keenetic.pro/rci/",
  "username": "api_updater",
  "password_env": "FQDN_UPDATER_ROUTER_HOME_PASSWORD",
  "enabled": true
}
```

Реальный пароль хранится в `.env`, `.env.secrets` или файле из `secrets/`.

## Services

Встроенный source registry хранит runtime raw-URL из `itdoginfo/allow-domains`. Списки не вендорятся в репозиторий.

## Mappings

Mapping связывает router, service и managed object-group:

```json
{
  "router_id": "home",
  "service_key": "telegram",
  "object_group_name": "svc-telegram",
  "route_target_type": "interface",
  "route_target_value": "Wireguard0",
  "exclusive": true,
  "auto": true,
  "managed": true
}
```

`sync` изменяет только mappings с `managed=true`.

## Runtime

`runtime.artifacts_dir`, `runtime.logs_dir` и `runtime.state_dir` задают локальные runtime-пути. `runtime.schedule` хранит daily/weekly/disabled расписание для systemd installer.
