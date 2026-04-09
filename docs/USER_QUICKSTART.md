# FQDN-updater: краткая инструкция пользователя

## Что это сейчас

На текущем этапе `fqdn-updater` — это рабочий Python-scaffold проекта с:

- генерацией стартового JSON-конфига;
- валидацией конфига;
- встроенным registry поддерживаемых сервисов;
- загрузкой и нормализацией upstream-списков на уровне внутренних модулей;
- read-only `dry-run` CLI-командой с human/json выводом и JSON artifact.

Это еще не готовый production sync-инструмент для Keenetic. Команды `sync` и `status` пока не реализованы.

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

- выполнять реальный sync на Keenetic через RCI API;
- применять diff и сохранять конфигурацию на роутере;
- запускать `sync` и `status` через CLI;
- поставлять Docker/systemd runtime для scheduled execution.

Также apply-методы `KeeneticRciClient` пока остаются не реализованными.

## Что уже есть внутри кода

В кодовой базе уже подготовлены внутренние строительные блоки:

- typed config schema;
- source registry и normalizer;
- run artifact model и persistence;
- read-only dry-run orchestration service и CLI entry point;
- resolver паролей из env/file.

Это полезная база для следующих slice, но не завершенный пользовательский продукт.
