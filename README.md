# FQDN-updater

[English](README_EN.md) | Русский

[![Verify](https://github.com/Spiceman161/fqdn-updater/actions/workflows/verify.yml/badge.svg?branch=main)](https://github.com/Spiceman161/fqdn-updater/actions/workflows/verify.yml)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm%20Noncommercial-blue.svg)](LICENSE)

**FQDN-updater** — source-available CLI-инструмент для безопасной синхронизации managed FQDN object-group, DNS-proxy route bindings и CIDR static routes на роутерах Keenetic через KeenDNS RCI API.

Проект рассчитан на небольшой VPS или домашний сервер. Оператор хранит конфиг и секреты локально, проверяет изменения через `status` и `dry-run`, применяет только явно управляемые mappings через `sync`, а регулярный запуск выполняет как одноразовый Docker Compose job под systemd timer.

## Модель безопасности

- Только Keenetic.
- Только KeenDNS RCI API по HTTPS с HTTP Digest Auth.
- Только отдельный low-privilege API-пользователь для опубликованного RCI web application.
- Любой apply сначала читает текущее состояние роутера, строит deterministic diff и только затем пишет изменения.
- Инструмент меняет только managed object-group, DNS route bindings и static routes, описанные в `config.json`.
- `status`, `dry-run`, журнал и read-only проверки панели не выполняют удалённых write-операций.
- В текущем scope нет web UI, daemon-процесса, уведомлений, SSH production transport и поддержки не-Keenetic устройств.

## Установка

На чистой Ubuntu 22.04 и новее:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash
```

Установка конкретного release tag:

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash -s -- --version v0.1.0
```

Installer разворачивает проект в `/opt/fqdn-updater`, сохраняет пользовательские `config.json`, `.env*`, `data/`, `secrets/` и `.venv`, ставит host-команды `fqdn-updater` и `domaingo`, собирает Docker image и устанавливает systemd timer.

## Обновление

После первой установки обновляйте проект командой:

```bash
fqdn-updater update
```

Она повторно запускает официальный installer, пересобирает Docker image и переустанавливает wrapper/systemd units. Пользовательские файлы остаются на месте.

Для фиксации на конкретном release tag:

```bash
fqdn-updater update --version v0.1.0
```

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

В панели можно создать конфиг, добавить роутер, сгенерировать пароль для RCI-пользователя, выбрать списки, обнаружить WireGuard-интерфейсы, настроить расписание и выполнить `dry-run` перед `sync`.

## Базовые команды

```bash
fqdn-updater config validate --config /opt/fqdn-updater/config.json
fqdn-updater status --config /opt/fqdn-updater/config.json
fqdn-updater dry-run --config /opt/fqdn-updater/config.json
fqdn-updater sync --config /opt/fqdn-updater/config.json
```

Host wrapper запускает `sync`, `dry-run` и `status` через Docker Compose. Команды управления (`panel`, `init`, `config`, `router`, `mapping`, `schedule`) выполняются через локальный Python venv в `/opt/fqdn-updater/.venv`.

Docker Compose runtime использует `config.json`, `.env*`, `secrets/` и `data/` как runtime-файлы.

`dry-run`, `sync`, `status`, `router list`, `mapping list` и `schedule show` поддерживают `--output json`, если нужен машинный вывод.

## Расписание

```bash
fqdn-updater schedule set-daily --config /opt/fqdn-updater/config.json --time 03:15 --timezone Europe/Moscow
sudo fqdn-updater schedule install --config /opt/fqdn-updater/config.json
```

Проверка timer и журнала:

```bash
systemctl status fqdn-updater.timer --no-pager
journalctl -u fqdn-updater.service -n 100 --no-pager
```

## KeenDNS RCI

В Keenetic web UI для `rci.<domain>` публикуется web application с protocol `HTTP` и портом `79`. В `config.json` хранится внешний endpoint вида `https://rci.<domain>/rci/`.

Используйте отдельного low-privilege пользователя для FQDN-updater. Реальные пароли храните в `.env.secrets`, `.env` или `secrets/`; не коммитьте production `config.json` и секреты.

## Документация

- [Навигация по документации](docs/README.md)
- [Быстрый старт оператора](docs/USER_QUICKSTART.md)
- [Панель](docs/PANEL.md)
- [Установка и эксплуатация](docs/DEPLOYMENT.md)
- [Конфигурация](docs/CONFIGURATION.md)
- [CLI reference](docs/CLI_REFERENCE.md)
- [Контекст для LLM/агентов](docs/LLM_CONTEXT.md)
- [Настройка KeenDNS RCI](docs/KEENETIC_RCI_SETUP.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)
- [Roadmap](docs/ROADMAP.md)
- [PRD](PRD.md)
- [Architecture](ARCHITECTURE.md)

## Лицензия

FQDN-updater распространяется по лицензии [PolyForm Noncommercial 1.0.0](LICENSE).

Это source-available/noncommercial проект, а не OSI-open-source лицензия. Некоммерческое использование, изучение и модификация разрешены условиями лицензии; коммерческое использование требует отдельного разрешения правообладателя.

Сторонние уведомления: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
