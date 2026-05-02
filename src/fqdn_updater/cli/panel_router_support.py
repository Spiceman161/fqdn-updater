from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from fqdn_updater.domain.config_schema import (
    AppConfig,
    RouterConfig,
    RouterServiceMappingConfig,
)
from fqdn_updater.infrastructure.secret_env_file import password_env_key_for_router_id

DEFAULT_SELECTED_SERVICES = frozenset(
    {
        "block_vpn_proxy_privacy",
        "block_news_politics",
        "block_other",
        "geoblock_ai",
        "geoblock_other",
        "hodca_network_os_tools",
        "hodca_ai_education_research",
        "hodca_other",
        "news",
        "cloudflare",
        "cloudfront",
        "digitalocean",
        "discord",
        "google_ai",
        "hdrezka",
        "hetzner",
        "meta",
        "ovh",
        "roblox",
        "telegram",
        "tiktok",
        "twitter",
        "youtube",
    }
)
DEFAULT_INTERFACE_NAME = "Wireguard0"
DEFAULT_RCI_TIMEOUT_SECONDS = 30
SERVICE_SELECTION_HINT_LINES = (
    "Для каждого выбранного сервиса будет создан свой список в разделе «Маршрутизация» Keenetic.",
    "Лимит доменов роутеров Keenetic составляет 1024 записи. "
    "Вам необходимо выбрать не более этого количества записей.",
    "Для IPv4+IPv6 действует отдельный лимит: около 4000 subnet-записей суммарно на роутер.",
)
ADD_ROUTER_HINT_LINES = ("Введите имя нового маршрутизатора.",)
ADD_ROUTER_RCI_URL_HINT_LINES = (
    "Нажмите кнопку копирования в новой строке «Доступ к веб-приложениям домашней сети».",
    "Скопированный URL должен начинаться с http://rci.",
)
ADD_ROUTER_USERNAME_HINT_LINES = (
    "Где взять RCI username: на Keenetic откройте раздел «Доменное имя».",
    "Проверьте, что доменное имя уже создано и включён доступ из Интернета.",
    "Создайте новый «Доступ к веб-приложениям домашней сети» с именем rci.",
    "Выберите «Авторизованный доступ», «Это устройство Keenetic», протокол HTTP и TCP порт 79.",
    "Добавьте нового пользователя и введите имя этого пользователя в поле ниже.",
)
ADD_ROUTER_PASSWORD_HINT_LINES = (
    "Сейчас задайте этот стойкий пароль новому Keenetic-пользователю, которого привязали к rci.",
    "Скопировать пароль можно через Ctrl+Shift+C.",
    "Поставьте галочку в столбце «Доступ» напротив нового пользователя и сохраните подключение.",
    "После этого вернитесь в мастер и продолжайте настройку KeenDNS RCI URL.",
)
EDIT_ROUTER_PASSWORD_HINT_LINES = (
    "Сейчас обновите пароль у low-privilege RCI пользователя на Keenetic.",
    "Скопировать пароль можно через Ctrl+Shift+C.",
    "После обновления пароля на Keenetic вернитесь в мастер и подтвердите шаг.",
)
ADD_ROUTER_SAVE_HINT_LINES = (
    "Проверьте введенные данные и подтвердите сохранение маршрутизатора.",
)
BASE_ROUTE_INTERFACE_HINT_LINES = (
    "Укажите маршрут, который будет использоваться по умолчанию для выбранных списков.",
)
GOOGLE_AI_OVERRIDE_HINT_LINES = (
    "Для корректной работы AI сервисов от Google можно указать другой отдельный интерфейс.",
)


@dataclass(frozen=True)
class RouteTargetDraft:
    route_target_type: Literal["interface", "gateway"]
    route_target_value: str
    route_interface: str | None = None

    def summary(self) -> str:
        if self.route_target_type == "interface":
            return f"interface:{self.route_target_value}"
        if self.route_interface:
            return f"gateway:{self.route_target_value} via {self.route_interface}"
        return f"gateway:{self.route_target_value}"


@dataclass(frozen=True)
class MappingPlan:
    default_target: RouteTargetDraft
    google_ai_target: RouteTargetDraft | None = None

    def target_for_service(self, service_key: str) -> RouteTargetDraft:
        if service_key == "google_ai" and self.google_ai_target is not None:
            return self.google_ai_target
        return self.default_target


def partition_router_mappings(
    *,
    config: AppConfig,
    router_id: str,
) -> tuple[list[RouterServiceMappingConfig], list[RouterServiceMappingConfig]]:
    enabled_services = {service.key for service in config.services if service.enabled}
    editable: list[RouterServiceMappingConfig] = []
    preserved: list[RouterServiceMappingConfig] = []
    for mapping in config.mappings:
        if mapping.router_id != router_id:
            continue
        if mapping.managed and mapping.service_key in enabled_services:
            editable.append(mapping)
        else:
            preserved.append(mapping)
    return editable, preserved


def derive_mapping_plan_defaults(
    *,
    editable_mappings: list[RouterServiceMappingConfig],
) -> tuple[RouteTargetDraft, bool, RouteTargetDraft | None]:
    default_targets = [
        _mapping_route_target(mapping)
        for mapping in sorted(editable_mappings, key=lambda item: item.service_key)
        if mapping.service_key != "google_ai"
    ]
    unique_default_targets = {
        (
            target.route_target_type,
            target.route_target_value,
            target.route_interface,
        )
        for target in default_targets
    }
    has_inconsistent_default = len(unique_default_targets) > 1

    if default_targets:
        default_target = default_targets[0]
    else:
        google_ai_mapping = next(
            (mapping for mapping in editable_mappings if mapping.service_key == "google_ai"),
            None,
        )
        if google_ai_mapping is None:
            default_target = RouteTargetDraft("interface", DEFAULT_INTERFACE_NAME, None)
        else:
            default_target = _mapping_route_target(google_ai_mapping)

    google_ai_override = None
    google_ai_mapping = next(
        (mapping for mapping in editable_mappings if mapping.service_key == "google_ai"),
        None,
    )
    if google_ai_mapping is not None:
        candidate_target = _mapping_route_target(google_ai_mapping)
        if candidate_target != default_target:
            google_ai_override = candidate_target

    return default_target, has_inconsistent_default, google_ai_override


def default_interface_target_value(default_target: RouteTargetDraft) -> str:
    if default_target.route_target_type == "interface":
        return default_target.route_target_value
    if default_target.route_interface:
        return default_target.route_interface
    return DEFAULT_INTERFACE_NAME


def derive_router_id(*, name: str, config: AppConfig) -> str:
    base_slug = _slugify_router_name(name)
    if _router_id_is_available(config=config, router_id=base_slug):
        return base_slug

    suffix = 2
    while True:
        candidate = f"{base_slug}-{suffix}"
        if _router_id_is_available(config=config, router_id=candidate):
            return candidate
        suffix += 1


def ensure_password_env_available(
    *,
    config: AppConfig,
    router_id: str,
    password_env: str,
) -> None:
    for router in config.routers:
        existing_password_env = _router_password_env_reference(router)
        if router.id != router_id and existing_password_env == password_env:
            raise RuntimeError(
                f"Password env '{password_env}' уже используется роутером '{router.id}'"
            )


def is_missing_password_env_error(message: str) -> bool:
    return "password env" in message and "is not set" in message


def _mapping_route_target(mapping: RouterServiceMappingConfig) -> RouteTargetDraft:
    return RouteTargetDraft(
        route_target_type=mapping.route_target_type,
        route_target_value=mapping.route_target_value,
        route_interface=mapping.route_interface,
    )


def _slugify_router_name(name: str) -> str:
    transliterated_name = "".join(
        _CYRILLIC_TO_ASCII.get(character, character) for character in name
    )
    normalized_name = unicodedata.normalize("NFKD", transliterated_name)
    ascii_name = normalized_name.encode("ascii", "ignore").decode("ascii")
    lowered_name = ascii_name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered_name).strip("-")
    if not slug:
        return "router"
    return slug


def _router_id_is_available(*, config: AppConfig, router_id: str) -> bool:
    existing_ids = {router.id for router in config.routers}
    if router_id in existing_ids:
        return False

    candidate_password_env = password_env_key_for_router_id(router_id)
    for router in config.routers:
        existing_password_env = _router_password_env_reference(router)
        if existing_password_env == candidate_password_env:
            return False
    return True


def _router_password_env_reference(router: RouterConfig) -> str | None:
    if router.password_env is not None:
        return router.password_env
    if router.password_file is not None:
        return password_env_key_for_router_id(router.id)
    return None


_CYRILLIC_TO_ASCII = {
    "А": "A",
    "а": "a",
    "Б": "B",
    "б": "b",
    "В": "V",
    "в": "v",
    "Г": "G",
    "г": "g",
    "Д": "D",
    "д": "d",
    "Е": "E",
    "е": "e",
    "Ё": "E",
    "ё": "e",
    "Ж": "Zh",
    "ж": "zh",
    "З": "Z",
    "з": "z",
    "И": "I",
    "и": "i",
    "Й": "I",
    "й": "i",
    "К": "K",
    "к": "k",
    "Л": "L",
    "л": "l",
    "М": "M",
    "м": "m",
    "Н": "N",
    "н": "n",
    "О": "O",
    "о": "o",
    "П": "P",
    "п": "p",
    "Р": "R",
    "р": "r",
    "С": "S",
    "с": "s",
    "Т": "T",
    "т": "t",
    "У": "U",
    "у": "u",
    "Ф": "F",
    "ф": "f",
    "Х": "Kh",
    "х": "kh",
    "Ц": "Ts",
    "ц": "ts",
    "Ч": "Ch",
    "ч": "ch",
    "Ш": "Sh",
    "ш": "sh",
    "Щ": "Shch",
    "щ": "shch",
    "Ъ": "",
    "ъ": "",
    "Ы": "Y",
    "ы": "y",
    "Ь": "",
    "ь": "",
    "Э": "E",
    "э": "e",
    "Ю": "Yu",
    "ю": "yu",
    "Я": "Ya",
    "я": "ya",
}
