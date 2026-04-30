from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from fqdn_updater.domain.config_schema import AppConfig, RouterServiceMappingConfig
from fqdn_updater.domain.keenetic_limits import validate_total_fqdn_entry_count
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.run_artifact import (
    FailureCategory,
    FailureDetail,
    RouterResultStatus,
    RouterRunResult,
    RunStatus,
    RunStep,
    ServiceResultStatus,
    ServiceRunResult,
)
from fqdn_updater.domain.source_loading import SourceLoadReport


def eligible_mappings(config: AppConfig) -> list[RouterServiceMappingConfig]:
    enabled_router_ids = {router.id for router in config.routers if router.enabled}
    enabled_service_keys = {service.key for service in config.services if service.enabled}
    return [
        mapping
        for mapping in config.mappings
        if mapping.managed
        and mapping.router_id in enabled_router_ids
        and mapping.service_key in enabled_service_keys
    ]


def group_source_failures(report: SourceLoadReport) -> dict[str, str]:
    failures_by_service: dict[str, list[str]] = {}
    for failure in report.failed:
        failures_by_service.setdefault(failure.service_key, []).append(
            f"{failure.source_url}: {failure.message}"
        )
    return {
        service_key: "; ".join(messages) for service_key, messages in failures_by_service.items()
    }


def static_route_capable_service_keys(config: AppConfig) -> set[str]:
    return {
        service.key
        for service in config.services
        if service.enabled
        and any(source.format in {"raw_cidr_list", "mixed"} for source in service.resolved_sources)
    }


def has_static_route_entries(entries: tuple[ObjectGroupEntry, ...]) -> bool:
    return any(entry.kind in {"ipv4_network", "ipv6_network"} for entry in entries)


def validate_router_desired_fqdn_total(
    *,
    router_id: str,
    mappings: Sequence[RouterServiceMappingConfig],
    desired_entries_by_service: dict[str, tuple[ObjectGroupEntry, ...]],
    source_failures_by_service: dict[str, str],
) -> None:
    total_entry_count = 0
    for mapping in mappings:
        if mapping.service_key in source_failures_by_service:
            continue
        desired_entries = desired_entries_by_service.get(mapping.service_key)
        if desired_entries is None:
            continue
        total_entry_count += sum(1 for entry in desired_entries if entry.kind == "domain")

    validate_total_fqdn_entry_count(router_id=router_id, entry_count=total_entry_count)


def build_failed_service_result(
    *,
    mapping: RouterServiceMappingConfig,
    failure_detail: FailureDetail,
    object_group_name: str | None = None,
) -> ServiceRunResult:
    return ServiceRunResult(
        service_key=mapping.service_key,
        object_group_name=object_group_name or mapping.object_group_name,
        status=ServiceResultStatus.FAILED,
        error_message=failure_detail.message,
        failure_detail=failure_detail,
    )


def build_failure_detail(
    *,
    step: RunStep,
    message: str,
    occurred_at: datetime,
    category: FailureCategory | None = None,
) -> FailureDetail:
    return FailureDetail(
        step=step,
        message=message,
        occurred_at=occurred_at,
        category=category,
    )


def aggregate_router_status(
    service_results: Sequence[ServiceRunResult],
) -> RouterResultStatus:
    if not service_results:
        return RouterResultStatus.NO_CHANGES

    statuses = {result.status for result in service_results}
    if statuses == {ServiceResultStatus.FAILED}:
        return RouterResultStatus.FAILED
    if statuses <= {ServiceResultStatus.NO_CHANGES, ServiceResultStatus.SKIPPED}:
        return RouterResultStatus.NO_CHANGES
    if ServiceResultStatus.FAILED not in statuses and ServiceResultStatus.UPDATED in statuses:
        return RouterResultStatus.UPDATED
    return RouterResultStatus.PARTIAL


def aggregate_run_status(router_results: Sequence[RouterRunResult]) -> RunStatus:
    if not router_results:
        return RunStatus.SUCCESS

    statuses = {result.status for result in router_results}
    if statuses == {RouterResultStatus.FAILED}:
        return RunStatus.FAILED
    if RouterResultStatus.FAILED not in statuses and RouterResultStatus.PARTIAL not in statuses:
        return RunStatus.SUCCESS
    return RunStatus.PARTIAL
