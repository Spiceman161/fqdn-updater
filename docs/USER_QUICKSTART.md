# FQDN-updater: краткая инструкция пользователя

## Что это сейчас

На текущем этапе `fqdn-updater` — это рабочий Python-scaffold проекта с:

- генерацией стартового JSON-конфига;
- валидацией конфига;
- встроенным registry поддерживаемых сервисов;
- загрузкой и нормализацией upstream-списков на уровне внутренних модулей;
- `dry-run` CLI-командой с human/json выводом и JSON artifact;
- `sync` CLI-командой для apply managed object-group diff и route binding через RCI;
- `status` CLI-командой для read-only диагностики enabled роутеров и DNS proxy;
- отдельными per-run логами в text/json режиме и JSON artifact с richer failure detail.
- Docker Compose runtime для одноразовых запусков CLI;
- примерами systemd service/timer для scheduled execution на VPS.

Это еще не полный production sync-инструмент для Keenetic: более глубокий `doctor`-режим пока не реализован.

## Требования

- Linux/macOS shell
- Python 3.12+
- виртуальное окружение `.venv` или совместимый Python runtime
- Docker и Docker Compose plugin для контейнерного запуска
- systemd на VPS для scheduled execution

Если зависимости еще не установлены:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

## Быстрый старт

На чистой Ubuntu 24.04 основной путь установки — one-command bootstrap installer:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash
```

Для установки конкретного release tag:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash -s -- --version v0.1.0
```

Installer разворачивает проект в `/opt/fqdn-updater`, сохраняет существующие `config.json`,
`.env*`, `data/`, `secrets/` и `.venv`, ставит host-команды `fqdn-updater` и `domaingo`,
собирает Docker image и включает systemd timer. Без аргументов `fqdn-updater` открывает panel;
`sync`, `dry-run` и `status` запускаются через Docker Compose, а команды управления конфигом
работают через локальный Python venv в `/opt/fqdn-updater/.venv`.

Показать доступные команды:

```bash
.venv/bin/python -m fqdn_updater --help
```

Создать scaffold-конфиг:

```bash
.venv/bin/python -m fqdn_updater init --config config.json
```

Проверить конфиг:

```bash
.venv/bin/python -m fqdn_updater config validate --config config.json
```

Запустить read-only dry-run:

```bash
.venv/bin/python -m fqdn_updater dry-run --config config.json
```

Запустить apply sync:

```bash
.venv/bin/python -m fqdn_updater sync --config config.json
```

Запустить read-only status diagnostics:

```bash
.venv/bin/python -m fqdn_updater status --config config.json
```

## KeenDNS RCI endpoint

Для RCI через KeenDNS используйте две разные настройки:

- в web-интерфейсе Keenetic для web application `rci.<domain>` выберите protocol `HTTP` и порт `79`;
- в `config.json` храните внешний endpoint как `https://rci.<domain>/rci/`.

Это нормальная схема: `fqdn-updater` подключается к опубликованному KeenDNS endpoint по HTTPS,
а KeenDNS/HTTP Proxy проксирует запрос к локальному RCI API роутера на HTTP-порт `79`.
Если в panel или `router add --rci-url` вставить адрес, скопированный из Keenetic как
`http://rci.<domain>`, инструмент сохранит его как `https://rci.<domain>/rci/`.

## Docker Compose runtime

Контейнерный runtime использует установленный CLI entrypoint `fqdn-updater`.
Это one-shot job, а не daemon и не web service.

Ожидаемые файлы в deployment-каталоге:

- `config.json` — рабочий конфиг, монтируется в контейнер как `/work/config.json` только для чтения;
- `.env` — локальные env-секреты для `password_env`, не коммитится;
- `secrets/` — локальные файлы секретов для `password_file`, монтируются как `/run/secrets/fqdn-updater`;
- `data/` — writable volume для артефактов и логов.

Перед первым Docker-запуском `config.json` должен уже существовать именно как файл:

```bash
test -f config.json
mkdir -p data secrets
```

Если Docker ранее успел создать `config.json` как директорию, удалите эту директорию и положите на ее место настоящий конфиг-файл.
В `compose.yaml` для config mount включен `create_host_path: false`, поэтому новый запуск больше не должен молча создавать директорию вместо отсутствующего файла.

Для Docker Compose удобно указывать runtime paths внутри mounted data directory:

```json
{
  "runtime": {
    "artifacts_dir": "/work/data/artifacts",
    "logs_dir": "/work/data/logs",
    "state_dir": "/work/data/state",
    "log_format": "text",
    "schedule": {
      "mode": "disabled",
      "times": [],
      "weekdays": [],
      "timezone": "UTC"
    }
  }
}
```

Если пароль хранится через env, укажите в router config имя переменной:

```json
{
  "password_env": "FQDN_UPDATER_ROUTER_MAIN_PASSWORD"
}
```

А в локальном `.env` рядом с `compose.yaml` задайте значение:

```dotenv
FQDN_UPDATER_ROUTER_MAIN_PASSWORD=replace-with-real-password
```

Если пароль хранится в файле, укажите путь внутри контейнера:

```json
{
  "password_file": "/run/secrets/fqdn-updater/router-main-password"
}
```

Локальный файл в таком случае лежит, например, в `secrets/router-main-password`.

Собрать image:

```bash
docker compose build fqdn-updater
```

Запустить read-only dry-run:

```bash
docker compose run --rm fqdn-updater dry-run --config /work/config.json
```

Запустить apply sync:

```bash
docker compose run --rm fqdn-updater sync --config /work/config.json
```

Запустить read-only status diagnostics:

```bash
docker compose run --rm fqdn-updater status --config /work/config.json
```

Команда по умолчанию в `compose.yaml` — `sync --config /work/config.json`, поэтому базовый one-shot
service можно запускать без дополнительных аргументов. Config-driven systemd unit при этом добавляет
`--trigger scheduled`, чтобы такие запуски отдельно маркировались в логах и артефактах.

## systemd timer

Примеры unit-файлов лежат в:

- `examples/fqdn-updater.service`
- `examples/fqdn-updater.timer`

Это статический baseline для ручной установки. Рекомендуемый путь для рабочих scheduled run:

```bash
fqdn-updater schedule set-daily --config config.json --time 03:15 --timezone Europe/Moscow
sudo fqdn-updater schedule install --config config.json
```

Для weekly-расписания:

```bash
fqdn-updater schedule set-weekly --config config.json --day mon --day fri --time 04:00 --timezone Europe/Moscow
sudo fqdn-updater schedule install --config config.json
```

Рекомендуемая схема deployment на VPS:

```bash
sudo mkdir -p /opt/fqdn-updater
sudo cp compose.yaml Dockerfile pyproject.toml README.md /opt/fqdn-updater/
sudo cp -r src examples /opt/fqdn-updater/
sudo install -m 0600 config.json /opt/fqdn-updater/config.json
sudo mkdir -p /opt/fqdn-updater/data /opt/fqdn-updater/secrets
```

Скопируйте реальные секреты отдельно и выставьте права так, чтобы их мог читать только root или service user.
Не добавляйте реальные `.env`, `config.json` и `secrets/` в git.

Установить example timer вручную:

```bash
sudo cp examples/fqdn-updater.service /etc/systemd/system/fqdn-updater.service
sudo cp examples/fqdn-updater.timer /etc/systemd/system/fqdn-updater.timer
sudo systemctl daemon-reload
sudo systemctl enable --now fqdn-updater.timer
```

Или доверить генерацию unit/timer самому приложению:

```bash
sudo fqdn-updater schedule install --config /opt/fqdn-updater/config.json
```

Ручной запуск того же one-shot sync:

```bash
sudo systemctl start fqdn-updater.service
```

Посмотреть journal:

```bash
journalctl -u fqdn-updater.service -n 100 --no-pager
```

Рабочее расписание теперь задаётся в `config.json` в `runtime.schedule`, а `schedule install`
рендерит нужные `OnCalendar=` строки и включает, перезапускает или выключает timer через `systemctl`.

## Что будет в config

Сгенерированный `config.json` содержит:

- `routers` — список роутеров;
- `services` — встроенные сервисы и upstream URL;
- `mappings` — привязки `router/service -> object-group/route`;
- `runtime` — базовые runtime-настройки, включая каталог артефактов.
- `runtime.logs_dir` — каталог отдельных run-логов;
- `runtime.state_dir` — каталог lock/state файлов, включая `run.lock`;
- `runtime.log_format` — формат логов (`text` или `json`).
- `runtime.schedule` — config-driven расписание для systemd timer.

Пример референс-конфига лежит в `examples/config.example.json`.

## Проверка репозитория

Локальная проверка кода и тестов:

```bash
./scripts/verify.sh
```

Этот wrapper запускает:

- `ruff format . --check`
- `ruff check .`
- `pytest`

## Текущие ограничения

На текущем этапе проект еще не умеет:

- выполнять расширенную удалённую диагностику client-side DNS/default policy через `doctor`.

Подтверждённые ограничения Keenetic для FQDN object-group уже проверяются в планировании:

- не больше 300 записей в одном `object-group fqdn`;
- не больше 1024 записей суммарно в управляемом FQDN-плане роутера.

Если один mapping превышает 300 записей, `dry-run`/`sync` автоматически строит shard-группы:
`object_group_name`, затем `object_group_name-2`, `object_group_name-3`, `object_group_name-4`.
Если upstream-список позже уменьшится, лишние shard-группы очищаются, а их route binding удаляется.
План выше общего лимита 1024 записей на роутер по-прежнему отклоняется до записи на роутер.

## Что уже есть внутри кода

В кодовой базе уже подготовлены внутренние строительные блоки:

- typed config schema;
- source registry и normalizer;
- run artifact model и persistence;
- dry-run/sync orchestration service и CLI entry points;
- resolver паролей из env/file.

Это полезная база для следующих slice, но не завершенный пользовательский продукт.
