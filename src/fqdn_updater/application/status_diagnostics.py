from __future__ import annotations

from typing import Protocol

from fqdn_updater.application.keenetic_client import KeeneticClientFactory
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig
from fqdn_updater.domain.run_artifact import RunStep
from fqdn_updater.domain.status_diagnostics import (
    OverallDiagnosticStatus,
    RouterDiagnosticStatus,
    RouterStatusDiagnostic,
    StatusDiagnosticsResult,
)


class RouterSecretResolver(Protocol):
    def resolve(self, router: RouterConfig) -> str:
        """Resolve the password for a router."""


class StatusDiagnosticsService:
    def __init__(
        self,
        *,
        secret_resolver: RouterSecretResolver,
        client_factory: KeeneticClientFactory,
    ) -> None:
        self._secret_resolver = secret_resolver
        self._client_factory = client_factory

    def check(self, *, config: AppConfig) -> StatusDiagnosticsResult:
        router_results = tuple(
            self._check_router(router=router) for router in config.routers if router.enabled
        )
        return StatusDiagnosticsResult(
            overall_status=_aggregate_overall_status(router_results),
            checked_router_count=len(router_results),
            router_results=router_results,
        )

    def _check_router(self, *, router: RouterConfig) -> RouterStatusDiagnostic:
        try:
            password = self._secret_resolver.resolve(router)
        except Exception as exc:
            return RouterStatusDiagnostic(
                router_id=router.id,
                status=RouterDiagnosticStatus.FAILED,
                error_message=str(exc),
                failure_step=RunStep.SECRET_RESOLVE,
            )

        try:
            client = self._client_factory.create(router=router, password=password)
        except Exception as exc:
            return RouterStatusDiagnostic(
                router_id=router.id,
                status=RouterDiagnosticStatus.FAILED,
                error_message=str(exc),
                failure_step=RunStep.CLIENT_CREATE,
            )

        try:
            dns_proxy_status = client.get_dns_proxy_status()
        except Exception as exc:
            return RouterStatusDiagnostic(
                router_id=router.id,
                status=RouterDiagnosticStatus.FAILED,
                error_message=str(exc),
                failure_step=RunStep.READ_DNS_PROXY_STATUS,
            )

        if dns_proxy_status.enabled:
            return RouterStatusDiagnostic(
                router_id=router.id,
                status=RouterDiagnosticStatus.HEALTHY,
                dns_proxy_enabled=True,
            )

        return RouterStatusDiagnostic(
            router_id=router.id,
            status=RouterDiagnosticStatus.DEGRADED,
            dns_proxy_enabled=False,
        )


def _aggregate_overall_status(
    router_results: tuple[RouterStatusDiagnostic, ...],
) -> OverallDiagnosticStatus:
    statuses = {result.status for result in router_results}
    if RouterDiagnosticStatus.FAILED in statuses:
        return OverallDiagnosticStatus.FAILED
    if RouterDiagnosticStatus.DEGRADED in statuses:
        return OverallDiagnosticStatus.DEGRADED
    return OverallDiagnosticStatus.HEALTHY
