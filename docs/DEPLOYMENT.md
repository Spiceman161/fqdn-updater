# Установка и эксплуатация

## Ubuntu 22.04+

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash
```

По умолчанию используется `/opt/fqdn-updater`. Host-команда `fqdn-updater` открывает панель без аргументов, а `sync`, `dry-run` и `status` запускает через Docker Compose.

## Обновление

На установленном сервере:

```bash
fqdn-updater update
```

Команда запускает тот же installer, что и первичная установка. Installer скачивает актуальный release, заменяет код в `/opt/fqdn-updater`, пересобирает Docker image и переустанавливает systemd units. Перед заменой он сохраняет пользовательские `config.json`, `.env*`, `data/`, `secrets/` и `.venv`, затем возвращает их обратно.

Можно повторно выполнить и прямую команду установки:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash
```

Для конкретного release tag:

```bash
fqdn-updater update --version v0.1.0
```

## Docker Compose runtime

Рабочие файлы:

- `config.json` монтируется в контейнер как `/work/config.json` только для чтения;
- `.env` передаёт Docker env-переменные, а `.env.secrets` монтируется как `/work/.env.secrets` и читается приложением;
- `secrets/` монтируется как `/run/secrets/fqdn-updater`;
- `data/` монтируется как writable volume для artifacts, logs и state.

```bash
docker compose build fqdn-updater
docker compose run --rm fqdn-updater dry-run --config /work/config.json
docker compose run --rm fqdn-updater sync --config /work/config.json
```

## systemd

```bash
fqdn-updater schedule set-daily --config /opt/fqdn-updater/config.json --time 03:15 --timezone Europe/Moscow
sudo fqdn-updater schedule install --config /opt/fqdn-updater/config.json
```

Проверка:

```bash
systemctl status fqdn-updater.timer --no-pager
journalctl -u fqdn-updater.service -n 100 --no-pager
```
