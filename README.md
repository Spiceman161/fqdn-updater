# FQDN-updater

[English](README_EN.md) | Русский

[![Verify](https://github.com/Spiceman161/fqdn-updater/actions/workflows/verify.yml/badge.svg?branch=main)](https://github.com/Spiceman161/fqdn-updater/actions/workflows/verify.yml)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE)

**FQDN-updater** — source-available CLI-инструмент для централизованной синхронизации управляемых FQDN object-group на роутерах Keenetic через KeenDNS RCI API.

Проект рассчитан на небольшой VPS или домашний сервер: оператор настраивает роутеры и списки локально, проверяет изменения через `dry-run`, применяет только явно управляемые object-group и route binding через `sync`, а регулярный запуск выполняет как одноразовый Docker/systemd job.

## Модель безопасности

- Только Keenetic.
- Только KeenDNS RCI API по HTTPS с HTTP Digest Auth.
- Только low-privilege API-пользователь для опубликованного RCI web application.
- Любой apply сначала читает текущее состояние роутера, строит детерминированный diff и только затем пишет изменения.
- Инструмент меняет только object-group и route binding, явно описанные в конфиге как managed.
- `status`, `dry-run`, экран журнала и проверки панели не выполняют удалённых write-операций.
- В MVP нет web UI, daemon-процесса, уведомлений, шардирования за пределами реализованных Keenetic FQDN-групп и production SSH transport.

## Установка

На чистой Ubuntu 22.04 и новее:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash
```

Установка конкретного release tag:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash -s -- --version v0.1.0
```

Installer разворачивает проект в `/opt/fqdn-updater`, сохраняет существующие `config.json`, `.env*`, `data/`, `secrets/` и `.venv`, ставит host-команды `fqdn-updater` и `domaingo`, собирает Docker image и устанавливает systemd timer.

## Первый запуск

Откройте панель:

```bash
fqdn-updater
```

Альтернативные входы:

```bash
domaingo
fqdn-updater panel --config /opt/fqdn-updater/config.json
```

В панели можно создать конфиг, добавить роутер, сгенерировать пароль для RCI-пользователя, выбрать списки, обнаружить WireGuard-интерфейсы и выполнить `dry-run` перед синхронизацией.

## Базовые команды

Проверить конфиг:

```bash
fqdn-updater config validate --config /opt/fqdn-updater/config.json
```

Проверить удалённые предусловия без записей на роутер:

```bash
fqdn-updater status --config /opt/fqdn-updater/config.json
```

Посмотреть план изменений:

```bash
fqdn-updater dry-run --config /opt/fqdn-updater/config.json
```

Применить managed-изменения:

```bash
fqdn-updater sync --config /opt/fqdn-updater/config.json
```

Host wrapper запускает `sync`, `dry-run` и `status` через Docker Compose. Команды управления (`panel`, `init`, `config`, `router`, `mapping`, `schedule`) выполняются через локальный Python venv в `/opt/fqdn-updater/.venv`.

Docker Compose runtime использует `config.json`, `.env*`, `secrets/` и `data/` как runtime-файлы.

## Расписание

Задать daily-расписание и установить systemd units:

```bash
fqdn-updater schedule set-daily --config /opt/fqdn-updater/config.json --time 03:15 --timezone Europe/Moscow
sudo fqdn-updater schedule install --config /opt/fqdn-updater/config.json
```

Проверить timer и журнал:

```bash
systemctl status fqdn-updater.timer --no-pager
journalctl -u fqdn-updater.service -n 100 --no-pager
```

## KeenDNS RCI

В Keenetic web UI для `rci.<domain>` публикуется web application с protocol `HTTP` и портом `79`. В `config.json` хранится внешний endpoint вида `https://rci.<domain>/rci/`.

Используйте отдельного low-privilege пользователя для FQDN-updater. Реальные пароли храните в `.env`, `.env.secrets` или `secrets/`; не коммитьте production `config.json` и секреты.

## Документация

- [Быстрый старт оператора](docs/USER_QUICKSTART.md)
- [Панель](docs/PANEL.md)
- [Установка и эксплуатация](docs/DEPLOYMENT.md)
- [Конфигурация](docs/CONFIGURATION.md)
- [CLI reference](docs/CLI_REFERENCE.md)
- [Настройка KeenDNS RCI](docs/KEENETIC_RCI_SETUP.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Roadmap](docs/ROADMAP.md)
- [PRD](PRD.md)
- [Architecture](ARCHITECTURE.md)

## Лицензия

FQDN-updater распространяется по лицензии [PolyForm Noncommercial 1.0.0](LICENSE).

Это source-available/noncommercial проект, а не OSI-open-source лицензия. Некоммерческое использование, изучение и модификация разрешены условиями лицензии; коммерческое использование требует отдельного разрешения правообладателя.

Сторонние уведомления: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
