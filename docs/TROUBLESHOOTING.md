# Troubleshooting

## `config.json` не найден

Откройте панель или создайте стартовый конфиг:

```bash
fqdn-updater panel --config /opt/fqdn-updater/config.json
fqdn-updater init --config /opt/fqdn-updater/config.json
```

Если команда запускается внутри Docker Compose, путь к config обычно `/work/config.json`.

## Docker создал `config.json` как директорию

Удалите директорию и положите настоящий файл `config.json`. В актуальном `compose.yaml` для mount включён `create_host_path: false`, поэтому новый runtime не должен создавать эту директорию автоматически.

## Ошибка Digest Auth

Проверьте:

- username в `config.json`;
- значение `password_env` в `.env.secrets` или `.env`;
- файл из `password_file`, если используется file secret;
- что этот пользователь привязан к опубликованному KeenDNS RCI web application;
- что URL сохранён как `https://.../rci/`.

## `status` показывает disabled DNS proxy

Включите DNS proxy на Keenetic. DNS-based route rules не будут работать корректно, если клиенты не используют Keenetic как DNS.

## WireGuard interface не находится

Проверьте RCI endpoint и пароль, затем выполните:

```bash
fqdn-updater status --config /opt/fqdn-updater/config.json
```

Если discovery недоступен, укажите interface или gateway вручную в панели или через `mapping set`.

## `dry-run` вернул exit code 30

Это не ошибка. Код `30` означает, что `dry-run` успешно нашёл изменения. Проверьте diff и запустите `sync`, если план ожидаемый.

## `sync` или `dry-run` вернул exit code 20

Код `20` означает partial/failed result. Смотрите:

- human output команды;
- JSON output через `--output json`;
- последний artifact в `data/artifacts`;
- полный лог в `data/logs`.

Ошибки одного роутера или сервиса не должны скрывать результаты остальных.

## Списки превышают FQDN-лимиты Keenetic

FQDN entries шардируются по 300 записей на group, но общий managed FQDN plan выше 1024 entries на роутер отклоняется до записи. Уменьшите набор services или используйте более узкие filtered services.

CIDR entries не учитываются как FQDN entries; они применяются как managed static routes.

## Расписание не установилось

`schedule install` пишет в `/etc/systemd/system`, поэтому обычно нужен `sudo`:

```bash
sudo fqdn-updater schedule install --config /opt/fqdn-updater/config.json
```

Если systemd недоступен, запускайте `sync` вручную или через другой host scheduler, сохраняя one-shot semantics.
