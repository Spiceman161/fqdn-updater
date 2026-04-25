# Быстрый старт оператора

Эта инструкция описывает happy-path для первого рабочего запуска FQDN-updater на Ubuntu 22.04 и новее.

## 1. Установите проект

```bash
curl -fsSL https://raw.githubusercontent.com/Spiceman161/fqdn-updater/main/install.sh | sudo bash
```

Проект будет установлен в `/opt/fqdn-updater`. Installer сохранит существующие `config.json`, `.env*`, `data/`, `secrets/` и `.venv`, если они уже есть.

## 2. Откройте панель

```bash
fqdn-updater
```

То же самое:

```bash
domaingo
fqdn-updater panel --config /opt/fqdn-updater/config.json
```

Если `config.json` отсутствует, панель предложит создать стартовый конфиг.

## 3. Подготовьте KeenDNS RCI

В Keenetic создайте web application для RCI:

- имя публикации: `rci`;
- протокол в Keenetic web UI: `HTTP`;
- локальный порт: `79`;
- доступ: авторизованный;
- пользователь: отдельный low-privilege пользователь, например `api_updater`.

В FQDN-updater сохраняется внешний URL вида `https://rci.<domain>/rci/`. Если вставить скопированный из Keenetic `http://rci.<domain>`, CLI и панель нормализуют его в HTTPS endpoint.

## 4. Добавьте роутер

В панели откройте `Маршрутизаторы` -> `Добавить новый маршрутизатор`.

Панель:

- предложит имя роутера;
- сгенерирует стойкий пароль для RCI-пользователя;
- покажет, куда вставить пароль в Keenetic;
- сохранит пароль в `.env.secrets` рядом с конфигом;
- сохранит в `config.json` только имя env-переменной, а не сам пароль.

## 5. Выберите списки и маршрут

Выберите сервисные списки, которые нужно вести на этом роутере. Для каждого выбранного сервиса FQDN-updater создаёт managed object-group и route binding.

Панель показывает счётчики доменов, IPv4 и IPv6, а также лимиты Keenetic:

- до 300 FQDN-записей в одном object-group;
- до 1024 managed FQDN-записей суммарно на роутер;
- около 4000 subnet-записей суммарно на роутер для IPv4+IPv6.

Для обычного сценария выберите WireGuard-интерфейс из discovery. Для `google_ai` можно задать отдельный route target override.

## 6. Проверьте без записи на роутер

```bash
fqdn-updater config validate --config /opt/fqdn-updater/config.json
fqdn-updater status --config /opt/fqdn-updater/config.json
fqdn-updater dry-run --config /opt/fqdn-updater/config.json
```

`status` и `dry-run` читают удалённое состояние, но не применяют изменения.

## 7. Примените sync

```bash
fqdn-updater sync --config /opt/fqdn-updater/config.json
```

`sync` меняет только managed object-group и route binding из конфига. Перед записью он читает текущее состояние, строит diff и применяет минимальные изменения.

## 8. Включите расписание

Этот сценарий использует systemd timer.

```bash
fqdn-updater schedule set-daily --config /opt/fqdn-updater/config.json --time 03:15 --timezone Europe/Moscow
sudo fqdn-updater schedule install --config /opt/fqdn-updater/config.json
```

Проверка:

```bash
systemctl status fqdn-updater.timer --no-pager
journalctl -u fqdn-updater.service -n 100 --no-pager
```

## Runtime-файлы

Docker Compose runtime использует те же runtime-пути в контейнере.

- `config.json` — локальный конфиг роутеров, сервисов, mappings и расписания.
- `.env.secrets` или `.env` — env-секреты паролей.
- `secrets/` — файловые секреты, если используются `password_file`.
- `data/artifacts` — JSON-артефакты запусков.
- `data/logs` — логи запусков.
- `data/state` — локальное состояние и cache.

Эти файлы не должны попадать в git.
