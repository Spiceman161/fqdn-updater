# Документация FQDN-updater

Это навигационный вход в документацию. Подробные документы ведутся на русском; `README_EN.md` — краткий англоязычный обзор проекта.

## Быстрый маршрут

- Первый рабочий запуск: [Быстрый старт оператора](USER_QUICKSTART.md).
- Интерактивная настройка: [Панель](PANEL.md).
- Установка, обновление и systemd: [Установка и эксплуатация](DEPLOYMENT.md).
- Структура `config.json`: [Конфигурация](CONFIGURATION.md).
- Все команды и exit codes: [CLI reference](CLI_REFERENCE.md).
- Подготовка Keenetic: [Настройка KeenDNS RCI](KEENETIC_RCI_SETUP.md).
- Частые ошибки: [Troubleshooting](TROUBLESHOOTING.md).

## Для разработчиков и LLM

- Архитектурные границы: [Architecture](../ARCHITECTURE.md).
- Продуктовый scope: [PRD](../PRD.md).
- Контекст для LLM/агентов: [LLM_CONTEXT.md](LLM_CONTEXT.md).
- Правила contribution и verification gate: [Contributing](../CONTRIBUTING.md).

## Статус документов

- Текущие операторские документы: `USER_QUICKSTART.md`, `PANEL.md`, `DEPLOYMENT.md`, `CONFIGURATION.md`, `CLI_REFERENCE.md`, `KEENETIC_RCI_SETUP.md`, `TROUBLESHOOTING.md`.
- Текущие проектные документы: `PRD.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `CONTRIBUTING.md`, `LLM_CONTEXT.md`.
- Исторический документ: `REFACTORING_PLAN.md`. Он описывает уже выполненный refactoring plan и не является текущим backlog.

## Runtime-файлы

Runtime state не входит в публичную документацию и не должен коммититься:

- `config.json` и `config.*.json`;
- `.env`, `.env.*`;
- `secrets/`;
- `data/`;
- локальные AI/editor state директории вроде `.codex/`, `.agents/`, `.claude/`.

Публичные примеры живут в `examples/`.
