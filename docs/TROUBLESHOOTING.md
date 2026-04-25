# Troubleshooting

## `config.json` не найден

Откройте панель или создайте стартовый конфиг:

```bash
fqdn-updater panel --config /opt/fqdn-updater/config.json
fqdn-updater init --config /opt/fqdn-updater/config.json
```

## Ошибка Digest Auth

Проверьте username, пароль в `.env.secrets` или `.env`, и что этот пользователь привязан к опубликованному KeenDNS RCI web application.

## `status` показывает disabled DNS proxy

Включите DNS proxy на Keenetic. DNS-based route rules не будут работать корректно, если клиенты не используют Keenetic как DNS.

## WireGuard interface не находится

Проверьте RCI endpoint и пароль, затем выполните `status`. Если discovery недоступен, укажите interface или gateway вручную.

## Docker создал `config.json` как директорию

Удалите директорию и положите настоящий файл `config.json`. В актуальном `compose.yaml` для mount включён `create_host_path: false`.
