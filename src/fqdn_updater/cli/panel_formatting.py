from __future__ import annotations

import shlex
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from rich.text import Text

from fqdn_updater.domain.config_schema import AppConfig, RouterConfig, ServiceDefinitionConfig
from fqdn_updater.domain.keenetic import RouteTargetCandidate
from fqdn_updater.domain.run_artifact import RouterResultStatus
from fqdn_updater.domain.source_loading import SourceLoadReport
from fqdn_updater.infrastructure.service_count_cache import ServiceEntryCountSnapshot

ROOT_PANEL_WIDTH = 86
DISCOVERY_ERROR_MESSAGE_LIMIT = 280
SERVICE_SELECTION_SERVICE_WIDTH = 22
SERVICE_SELECTION_COUNT_WIDTH = 7
KEENETIC_DOMAIN_SELECTION_LIMIT = 1024
SERVICE_SELECTION_GROUPS = {
    "block": (
        "block_p2p_streaming",
        "block_vpn_proxy_privacy",
        "block_dev_hosting_security",
        "block_finance_shopping",
        "block_social_creators",
        "block_news_politics",
        "block_other",
    ),
    "geoblock": (
        "geoblock_ai",
        "geoblock_dev_cloud_saas",
        "geoblock_media_games",
        "geoblock_shopping_travel",
        "geoblock_enterprise_hardware",
        "geoblock_security_networking",
        "geoblock_finance_payments",
        "geoblock_health_reference",
        "geoblock_other",
    ),
    "hodca": (
        "hodca_dev_cloud_saas",
        "hodca_network_os_tools",
        "hodca_media_games",
        "hodca_ai_education_research",
        "hodca_social_lifestyle",
        "hodca_finance_shopping",
        "hodca_other",
    ),
}
SERVICE_DISPLAY_LABELS = {
    "block": "block (full)",
    "block_p2p_streaming": "   p2p/media",
    "block_vpn_proxy_privacy": "   vpn/privacy",
    "block_dev_hosting_security": "   dev/hosting",
    "block_finance_shopping": "   finance/shop",
    "block_social_creators": "   social/media",
    "block_news_politics": "   news/politics",
    "block_other": "   other",
    "geoblock": "geoblock (full)",
    "geoblock_ai": "   AI tools",
    "geoblock_dev_cloud_saas": "   dev/SaaS",
    "geoblock_media_games": "   media/games",
    "geoblock_shopping_travel": "   shopping/travel",
    "geoblock_enterprise_hardware": "   enterprise",
    "geoblock_security_networking": "   security/net",
    "geoblock_finance_payments": "   payments",
    "geoblock_health_reference": "   health/ref",
    "geoblock_other": "   other",
    "hodca": "H.O.D.C.A (full)",
    "hodca_dev_cloud_saas": "   dev/cloud/SaaS",
    "hodca_network_os_tools": "   network/OS/tools",
    "hodca_media_games": "   media/games",
    "hodca_ai_education_research": "   AI/education",
    "hodca_social_lifestyle": "   social/lifestyle",
    "hodca_finance_shopping": "   finance/shop",
    "hodca_other": "   other",
    "meta": "meta (whatsapp)",
}


@dataclass(frozen=True)
class ServiceEntryCounts:
    domains: int | None
    ipv4: int | None
    ipv6: int | None


def _find_router(*, config: AppConfig, router_id: str) -> RouterConfig | None:
    for router in config.routers:
        if router.id == router_id:
            return router
    return None


def _route_candidate_title(candidate: RouteTargetCandidate) -> str:
    return " | ".join(
        (
            candidate.display_name or candidate.value,
            "connected" if candidate.connected else "not connected",
            candidate.status or "-",
            candidate.detail or "-",
        )
    )


def _service_entry_counts_from_report(
    *,
    services: list[ServiceDefinitionConfig],
    report: SourceLoadReport,
) -> dict[str, ServiceEntryCounts]:
    loaded_counts = {
        source.service_key: _service_entry_counts_from_snapshot(
            ServiceEntryCountSnapshot(
                domains=sum(1 for entry in source.typed_entries if entry.kind == "domain"),
                ipv4=sum(1 for entry in source.typed_entries if entry.kind == "ipv4_network"),
                ipv6=sum(1 for entry in source.typed_entries if entry.kind == "ipv6_network"),
            )
        )
        for source in report.loaded
    }
    failed_service_keys = {failure.service_key for failure in report.failed}
    return {
        service.key: (
            ServiceEntryCounts(domains=None, ipv4=None, ipv6=None)
            if service.key in failed_service_keys
            else loaded_counts.get(
                service.key,
                ServiceEntryCounts(domains=None, ipv4=None, ipv6=None),
            )
        )
        for service in services
    }


def _service_entry_counts_from_snapshot(
    snapshot: ServiceEntryCountSnapshot | None,
) -> ServiceEntryCounts:
    if snapshot is None:
        return ServiceEntryCounts(domains=None, ipv4=None, ipv6=None)
    return ServiceEntryCounts(
        domains=snapshot.domains,
        ipv4=snapshot.ipv4,
        ipv6=snapshot.ipv6,
    )


def _service_selection_title(
    *,
    service_key: str,
    counts: ServiceEntryCounts | None,
) -> str:
    counts = counts or ServiceEntryCounts(domains=None, ipv4=None, ipv6=None)
    return (
        f"{_service_display_label(service_key):<{SERVICE_SELECTION_SERVICE_WIDTH}} "
        f"| {_format_entry_count(counts.domains):>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {_format_entry_count(counts.ipv4):>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {_format_entry_count(counts.ipv6):>{SERVICE_SELECTION_COUNT_WIDTH}}"
    )


def _format_entry_count(value: int | None) -> str:
    if value is None:
        return "?"
    return str(value)


def _service_display_label(service_key: str) -> str:
    return SERVICE_DISPLAY_LABELS.get(service_key, service_key)


def _format_service_list(service_keys: Iterable[str]) -> str:
    return ", ".join(_service_display_label(service_key) for service_key in service_keys)


def _service_selection_header() -> str:
    return (
        f"{'Сервис':<{SERVICE_SELECTION_SERVICE_WIDTH}} "
        f"| {'домены':>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {'IPv4':>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {'IPv6':>{SERVICE_SELECTION_COUNT_WIDTH}}"
    )


def _service_selection_totals_line(
    *,
    selected_values: tuple[str, ...],
    service_counts: dict[str, ServiceEntryCounts],
) -> str | list[tuple[str, str]]:
    effective_selected_values = tuple(_effective_service_selection(selected_values))
    if not effective_selected_values:
        totals = ServiceEntryCounts(domains=0, ipv4=0, ipv6=0)
    else:
        totals = ServiceEntryCounts(
            domains=_sum_entry_counts(
                service_counts.get(service_key, ServiceEntryCounts(None, None, None)).domains
                for service_key in effective_selected_values
            ),
            ipv4=_sum_entry_counts(
                service_counts.get(service_key, ServiceEntryCounts(None, None, None)).ipv4
                for service_key in effective_selected_values
            ),
            ipv6=_sum_entry_counts(
                service_counts.get(service_key, ServiceEntryCounts(None, None, None)).ipv6
                for service_key in effective_selected_values
            ),
        )

    domain_count = _format_entry_count(totals.domains)
    prefix = f"{'Итого выбрано':<{SERVICE_SELECTION_SERVICE_WIDTH}} | "
    suffix = (
        f" | {_format_entry_count(totals.ipv4):>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {_format_entry_count(totals.ipv6):>{SERVICE_SELECTION_COUNT_WIDTH}}"
    )
    if totals.domains is not None and totals.domains > KEENETIC_DOMAIN_SELECTION_LIMIT:
        return [
            ("class:footer", prefix),
            ("fg:#ff5f5f bold", f"{domain_count:>{SERVICE_SELECTION_COUNT_WIDTH}}"),
            ("class:footer", suffix),
        ]

    return (
        f"{'Итого выбрано':<{SERVICE_SELECTION_SERVICE_WIDTH}} "
        f"| {domain_count:>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {_format_entry_count(totals.ipv4):>{SERVICE_SELECTION_COUNT_WIDTH}} "
        f"| {_format_entry_count(totals.ipv6):>{SERVICE_SELECTION_COUNT_WIDTH}}"
    )


def _enabled_service_selection_groups(
    enabled_service_keys: set[str],
) -> dict[str, tuple[str, ...]]:
    return {
        parent: children
        for parent, children in SERVICE_SELECTION_GROUPS.items()
        if parent in enabled_service_keys
        and all(child in enabled_service_keys for child in children)
    }


def _effective_service_selection(selected_values: Iterable[str]) -> set[str]:
    selected = set(selected_values)
    for parent, children in SERVICE_SELECTION_GROUPS.items():
        child_set = set(children)
        if parent in selected:
            selected.difference_update(child_set)
        elif child_set.issubset(selected):
            selected.difference_update(child_set)
            selected.add(parent)
    return selected


def _sum_entry_counts(values: Iterable[int | None]) -> int | None:
    total = 0
    for value in values:
        if value is None:
            return None
        total += value
    return total


def _router_state_label(enabled: bool) -> str:
    if enabled:
        return "[bold green]включён[/bold green]"
    return "[bold yellow]выключен[/bold yellow]"


def _router_state_plain(enabled: bool) -> str:
    return "включён" if enabled else "выключен"


def _router_selection_column_widths(routers: Iterable[RouterConfig]) -> tuple[int, int]:
    router_list = list(routers)
    return (
        max(_display_width(router.id) for router in router_list),
        max(_display_width(router.name) for router in router_list),
    )


def _router_selection_title(
    *,
    router: RouterConfig,
    router_id_width: int,
    router_name_width: int,
) -> str:
    return (
        f"{_pad_display(router.id, width=router_id_width)} | "
        f"{_pad_display(router.name, width=router_name_width)} | "
        f"{_router_state_plain(router.enabled)}"
    )


def _router_selection_header(*, router_id_width: int, router_name_width: int) -> str:
    return f"{'Маршрутизатор':<{router_id_width}} | {'Имя':<{router_name_width}} | Статус"


def _router_toggle_title(
    *,
    router: RouterConfig,
    router_id_width: int,
    router_name_width: int,
) -> str:
    return (
        f"{_pad_display(router.id, width=router_id_width)} | "
        f"{_pad_display(router.name, width=router_name_width)}"
    )


def _router_toggle_header(*, router_id_width: int, router_name_width: int) -> str:
    return f"{'Маршрутизатор':<{router_id_width}} | {'Имя':<{router_name_width}}"


def _router_toggle_summary(*, selected_values: tuple[str, ...], total: int) -> str:
    enabled_count = len(selected_values)
    disabled_count = total - enabled_count
    return f"Будет включено: {enabled_count} | выключено: {disabled_count}"


def _manual_run_selection_summary(*, selected_values: tuple[str, ...]) -> str:
    return f"Будет запущено: {len(selected_values)}"


def _pad_display(value: str, *, width: int) -> str:
    return value + (" " * max(width - _display_width(value), 0))


def _display_width(value: str) -> int:
    width = 0
    for character in value:
        if unicodedata.combining(character):
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
    return width


def _format_connected(value: bool | None) -> str:
    if value is None:
        return "[dim]-[/dim]"
    if value:
        return "[green]да[/green]"
    return "[yellow]нет[/yellow]"


def _format_dashboard_last_run_at(value: datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M")


def _format_dashboard_router_run_status(status: RouterResultStatus) -> str:
    if status in {RouterResultStatus.UPDATED, RouterResultStatus.NO_CHANGES}:
        return "[green]ok[/green]"
    if status is RouterResultStatus.PARTIAL:
        return "[yellow]partial[/yellow]"
    return "[red]fail[/red]"


def _format_dns_proxy(value: bool | None) -> str:
    if value is None:
        return "[dim]unknown[/dim]"
    if value:
        return "[green]включён[/green]"
    return "[yellow]выключен[/yellow]"


def _format_router_diagnostic_error(error_message: str | None) -> Text:
    if not error_message:
        return Text("-", style="dim")
    return Text(_truncate_discovery_error_message(error_message), style="red")


def _format_validation_error(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return str(exc)
    first_error = errors[0]
    location = ".".join(str(part) for part in first_error.get("loc", ()))
    message = str(first_error.get("msg", exc))
    if location:
        return f"{location}: {message}"
    return message


def _truncate_discovery_error_message(message: str) -> str:
    normalized_message = " ".join(message.split())
    if len(normalized_message) <= DISCOVERY_ERROR_MESSAGE_LIMIT:
        return normalized_message
    truncated = normalized_message[: DISCOVERY_ERROR_MESSAGE_LIMIT - 1].rstrip()
    return f"{truncated}…"


def _shell_quote_path(path: Path | str) -> str:
    return shlex.quote(str(path))
