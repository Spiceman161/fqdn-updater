# FQDN-updater: краткая инструкция пользователя

## Что это сейчас

На текущем этапе `fqdn-updater` — это рабочий Python-scaffold проекта с:

- генерацией стартового JSON-конфига;
- валидацией конфига;
- встроенным registry поддерживаемых сервисов;
- загрузкой и нормализацией upstream-списков на уровне внутренних модулей;
- `dry-run` CLI-командой с human/json выводом и JSON artifact;
- `sync` CLI-командой для apply managed object-group diff через RCI.

Это еще не полный production sync-инструмент для Keenetic: route binding apply и `status` пока не реализованы.

## Требования

- Linux/macOS shell
- Python 3.12+
- виртуальное окружение `.venv` или совместимый Python runtime

Если зависимости еще не установлены:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

## Быстрый старт

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

## Что будет в config

Сгенерированный `config.json` содержит:

- `routers` — список роутеров;
- `services` — встроенные сервисы и upstream URL;
- `mappings` — привязки `router/service -> object-group/route`;
- `runtime` — базовые runtime-настройки, включая каталог артефактов.

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

- поддерживать route binding apply для managed object-group;
- запускать `status` через CLI;
- поставлять Docker/systemd runtime для scheduled execution.

Также apply-путь пока ограничен object-group create/add/remove и не трогает route bindings.

## Что уже есть внутри кода

В кодовой базе уже подготовлены внутренние строительные блоки:

- typed config schema;
- source registry и normalizer;
- run artifact model и persistence;
- dry-run/sync orchestration service и CLI entry points;
- resolver паролей из env/file.

Это полезная база для следующих slice, но не завершенный пользовательский продукт.
