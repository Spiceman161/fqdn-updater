# Установка и эксплуатация

## Ubuntu 22.04+

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/v1.0.2/install.sh | sudo bash -s -- --version v1.0.2
```

По умолчанию используется `/opt/fqdn-updater`. Installer требует systemd, ставит Docker Engine/Compose plugin при необходимости, создаёт Python venv, собирает Docker image и устанавливает host wrapper `/usr/local/bin/fqdn-updater` плюс alias `domaingo`. Production-установка должна идти из versioned release tag; запуск installer без `--version` устанавливает latest GitHub Release и завершается с ошибкой, если latest release нельзя определить.

Wrapper без аргументов открывает панель. `sync`, `dry-run` и `status` запускаются через Docker Compose, чтобы scheduled runtime совпадал с ручным runtime.

## Обновление

На установленном сервере:

```bash
fqdn-updater update
```

Wrapper запускает локальный installer `/opt/fqdn-updater/install.sh` через временную копию, чтобы обновление не зависело от файла, который deployment может заменить. Installer скачивает latest GitHub Release, заменяет код в `/opt/fqdn-updater`, пересобирает Docker image и переустанавливает systemd units. Если latest release недоступен или GitHub вернул некорректные metadata, update завершается с ошибкой до скачивания кода проекта; fallback на `main` отсутствует.

Перед заменой он сохраняет пользовательские `config.json`, `.env*`, `data/`, `secrets/` и `.venv`, затем возвращает их обратно.

Для конкретного release tag:

```bash
fqdn-updater update --version v1.0.2
```

Если `/opt/fqdn-updater/install.sh` отсутствует или недоступен для чтения, wrapper завершится с ошибкой и покажет точную команду ручной переустановки для Ubuntu 22.04+:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/v1.0.2/install.sh | sudo bash -s -- --version v1.0.2
```

## Docker Compose runtime

Рабочие файлы:

- `config.json` монтируется в контейнер как `/work/config.json` read-only;
- `.env` передаёт Docker env-переменные;
- `.env.secrets` монтируется как `/work/.env.secrets` read-only и читается приложением;
- `secrets/` монтируется как `/run/secrets/fqdn-updater` read-only;
- `data/` монтируется как writable volume для artifacts, logs и state.

`compose.yaml` использует `create_host_path: false` для `config.json` и `.env.secrets`, чтобы Docker не создал директорию вместо отсутствующего файла.

Примеры:

```bash
docker compose build fqdn-updater
docker compose run --rm fqdn-updater status --config /work/config.json
docker compose run --rm fqdn-updater dry-run --config /work/config.json
docker compose run --rm fqdn-updater sync --config /work/config.json
```

## systemd

Настройка расписания:

```bash
fqdn-updater schedule set-daily --config /opt/fqdn-updater/config.json --time 03:15 --timezone Europe/Moscow
sudo fqdn-updater schedule install --config /opt/fqdn-updater/config.json
```

`schedule install` рендерит:

- `/etc/systemd/system/fqdn-updater.service`;
- `/etc/systemd/system/fqdn-updater.timer`.

Service запускает:

```text
/usr/bin/docker compose run --rm fqdn-updater sync --trigger scheduled --config /work/config.json
```

Проверка:

```bash
systemctl status fqdn-updater.timer --no-pager
journalctl -u fqdn-updater.service -n 100 --no-pager
```

Отключение расписания:

```bash
fqdn-updater schedule disable --config /opt/fqdn-updater/config.json
sudo fqdn-updater schedule install --config /opt/fqdn-updater/config.json
```

## Локальная разработка

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
./scripts/verify.sh
```

Production installer использует host Python venv для management-команд и Docker image для runtime-команд.
