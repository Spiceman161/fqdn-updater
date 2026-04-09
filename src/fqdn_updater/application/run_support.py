from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from fqdn_updater.domain.config_schema import AppConfig, RouterServiceMappingConfig
from fqdn_updater.domain.run_artifact import (
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


def build_failed_service_result(
    *,
    mapping: RouterServiceMappingConfig,
    failure_detail: FailureDetail,
) -> ServiceRunResult:
    return ServiceRunResult(
        service_key=mapping.service_key,
        object_group_name=mapping.object_group_name,
        status=ServiceResultStatus.FAILED,
        error_message=failure_detail.message,
        failure_detail=failure_detail,
    )


def build_failure_detail(
    *,
    step: RunStep,
    message: str,
    occurred_at: datetime,
) -> FailureDetail:
    return FailureDetail(step=step, message=message, occurred_at=occurred_at)


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
