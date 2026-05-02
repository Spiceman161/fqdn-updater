from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from fqdn_updater.application.keenetic_client import KeeneticClient, KeeneticClientFactory
from fqdn_updater.application.run_support import (
    aggregate_router_status,
    build_failed_service_result,
    build_failure_detail,
    eligible_mappings,
    group_source_failures,
    has_static_route_entries,
    static_route_capable_service_keys,
    validate_router_desired_fqdn_total,
)
from fqdn_updater.application.service_sync_planning import ServiceSyncPlan, ServiceSyncPlanner
from fqdn_updater.application.transport_failure import classify_transport_failure
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig, RouterServiceMappingConfig
from fqdn_updater.domain.keenetic import ObjectGroupState, RouteBindingState
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.object_group_sharding import managed_shard_names
from fqdn_updater.domain.run_artifact import (
    FailureDetail,
    RouterRunResult,
    RunArtifact,
    RunMode,
    RunStep,
    RunTrigger,
    ServiceResultStatus,
    ServiceRunResult,
)
from fqdn_updater.domain.source_loading import SourceLoadReport
from fqdn_updater.domain.static_route_diff import StaticRouteState


class SourceLoader(Protocol):
    def load_enabled_services(self, services: Sequence[object]) -> SourceLoadReport:
        """Load normalized entries for enabled services."""


class RouterSecretResolver(Protocol):
    def resolve(self, router: RouterConfig) -> str:
        """Resolve the password for a router."""


class RunArtifactWriter(Protocol):
    def write(self, config: AppConfig, artifact: RunArtifact) -> Path:
        """Persist a run artifact and return its path."""


class RunLogger(Protocol):
    @property
    def path(self) -> Path:
        """Return the run log path."""

    def event(
        self,
        event: str,
        *,
        step: RunStep | None = None,
        router_id: str | None = None,
        service_key: str | None = None,
        object_group_name: str | None = None,
        status: str | None = None,
        message: str | None = None,
    ) -> None:
        """Emit a run-scoped log event."""

    def close(self) -> None:
        """Close resources owned by the logger."""


class LoggerFactory(Protocol):
    def create(
        self,
        *,
        config: AppConfig,
        run_id: str,
        mode: RunMode,
        trigger: RunTrigger,
    ) -> RunLogger:
        """Create a run-scoped logger."""


class RunPlanningSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    managed_mappings: tuple[RouterServiceMappingConfig, ...] = Field(default_factory=tuple)
    desired_entries_by_service: dict[str, tuple[ObjectGroupEntry, ...]] = Field(
        default_factory=dict
    )
    source_failures_by_service: dict[str, str] = Field(default_factory=dict)
    static_route_service_keys: frozenset[str] = Field(default_factory=frozenset)

    def mappings_for_router(self, router_id: str) -> tuple[RouterServiceMappingConfig, ...]:
        return tuple(mapping for mapping in self.managed_mappings if mapping.router_id == router_id)


class RouterPlanningSession(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    router: RouterConfig
    mappings: tuple[RouterServiceMappingConfig, ...] = Field(default_factory=tuple)
    client: object | None = None
    startup_failure: FailureDetail | None = None

    @property
    def is_ready(self) -> bool:
        return self.client is not None and self.startup_failure is None


class ServicePlanningOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping: RouterServiceMappingConfig
    plans: tuple[ServiceSyncPlan, ...] = Field(default_factory=tuple)
    failed_service_result: ServiceRunResult | None = None
    router_failure_detail: FailureDetail | None = None


class RunPlanningService:
    def __init__(
        self,
        *,
        source_loader: SourceLoader,
        secret_resolver: RouterSecretResolver,
        client_factory: KeeneticClientFactory,
        planner: ServiceSyncPlanner,
        failure_time_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._source_loader = source_loader
        self._secret_resolver = secret_resolver
        self._client_factory = client_factory
        self._planner = planner
        self._failure_time_provider = failure_time_provider or _utc_now

    def prepare_run(self, *, config: AppConfig) -> RunPlanningSnapshot:
        loaded_sources = self._source_loader.load_enabled_services(config.services)
        return RunPlanningSnapshot(
            managed_mappings=tuple(eligible_mappings(config=config)),
            desired_entries_by_service={
                source.service_key: source.typed_entries for source in loaded_sources.loaded
            },
            source_failures_by_service=group_source_failures(report=loaded_sources),
            static_route_service_keys=frozenset(static_route_capable_service_keys(config)),
        )

    def start_router(
        self,
        *,
        logger: RunLogger,
        router: RouterConfig,
        mappings: Sequence[RouterServiceMappingConfig],
        snapshot: RunPlanningSnapshot,
    ) -> RouterPlanningSession:
        router_mappings = tuple(mappings)
        logger.event("router_started", router_id=router.id, status="started")

        try:
            password = self._secret_resolver.resolve(router)
        except Exception as exc:
            return self._failed_router_session(
                logger=logger,
                router=router,
                mappings=router_mappings,
                step=RunStep.SECRET_RESOLVE,
                message=str(exc),
            )

        try:
            client = self._client_factory.create(router=router, password=password)
        except Exception as exc:
            return self._failed_router_session(
                logger=logger,
                router=router,
                mappings=router_mappings,
                step=RunStep.CLIENT_CREATE,
                message=str(exc),
            )

        try:
            validate_router_desired_fqdn_total(
                router_id=router.id,
                mappings=router_mappings,
                desired_entries_by_service=snapshot.desired_entries_by_service,
                source_failures_by_service=snapshot.source_failures_by_service,
            )
        except Exception as exc:
            return self._failed_router_session(
                logger=logger,
                router=router,
                mappings=router_mappings,
                client=client,
                step=RunStep.PLAN_SERVICE,
                message=str(exc),
            )

        try:
            client.get_dns_proxy_status()
        except Exception as exc:
            return self._failed_router_session(
                logger=logger,
                router=router,
                mappings=router_mappings,
                client=client,
                step=RunStep.READ_DNS_PROXY_STATUS,
                message=f"Router preflight failed: {exc}",
            )

        return RouterPlanningSession(router=router, mappings=router_mappings, client=client)

    def plan_mapping(
        self,
        *,
        logger: RunLogger,
        session: RouterPlanningSession,
        snapshot: RunPlanningSnapshot,
        mapping: RouterServiceMappingConfig,
    ) -> ServicePlanningOutcome:
        if session.client is None:
            raise RuntimeError("router planning session is not ready")

        failure_message = snapshot.source_failures_by_service.get(mapping.service_key)
        if failure_message is not None:
            return self._service_failure_outcome(
                logger=logger,
                router=session.router,
                mapping=mapping,
                step=RunStep.SOURCE_LOAD,
                message=failure_message,
            )

        desired_entries = snapshot.desired_entries_by_service.get(mapping.service_key)
        if desired_entries is None:
            return self._service_failure_outcome(
                logger=logger,
                router=session.router,
                mapping=mapping,
                step=RunStep.SOURCE_LOAD,
                message=f"Missing loaded entries for service '{mapping.service_key}'",
            )

        actual_states: dict[str, ObjectGroupState] = {}
        for object_group_name in managed_shard_names(mapping.object_group_name):
            try:
                actual_states[object_group_name] = session.client.get_object_group(
                    object_group_name
                )
            except Exception as exc:
                return self._service_failure_outcome(
                    logger=logger,
                    router=session.router,
                    mapping=mapping,
                    step=RunStep.READ_OBJECT_GROUP,
                    message=str(exc),
                    object_group_name=object_group_name,
                    router_break_on_transport=True,
                )

        actual_route_bindings: dict[str, RouteBindingState] = {}
        for object_group_name in managed_shard_names(mapping.object_group_name):
            try:
                actual_route_bindings[object_group_name] = session.client.get_route_binding(
                    object_group_name
                )
            except Exception as exc:
                return self._service_failure_outcome(
                    logger=logger,
                    router=session.router,
                    mapping=mapping,
                    step=RunStep.READ_ROUTE_BINDING,
                    message=str(exc),
                    object_group_name=object_group_name,
                    router_break_on_transport=True,
                )

        actual_static_routes: tuple[StaticRouteState, ...] = ()
        if mapping.service_key in snapshot.static_route_service_keys or has_static_route_entries(
            desired_entries
        ):
            try:
                actual_static_routes = session.client.get_static_routes()
            except Exception as exc:
                return self._service_failure_outcome(
                    logger=logger,
                    router=session.router,
                    mapping=mapping,
                    step=RunStep.READ_STATIC_ROUTES,
                    message=str(exc),
                    router_break_on_transport=True,
                )

        try:
            mapping_plans = self._planner.plan_mapping(
                mapping=mapping,
                desired_entries=desired_entries,
                actual_states=actual_states,
                actual_route_bindings=actual_route_bindings,
                actual_static_routes=actual_static_routes,
            )
        except Exception as exc:
            return self._service_failure_outcome(
                logger=logger,
                router=session.router,
                mapping=mapping,
                step=RunStep.PLAN_SERVICE,
                message=str(exc),
            )

        return ServicePlanningOutcome(mapping=mapping, plans=mapping_plans)

    def _failed_router_session(
        self,
        *,
        logger: RunLogger,
        router: RouterConfig,
        mappings: tuple[RouterServiceMappingConfig, ...],
        step: RunStep,
        message: str,
        client: KeeneticClient | None = None,
    ) -> RouterPlanningSession:
        failure_detail = self._failure_detail(step=step, message=message)
        log_router_failure(logger=logger, router=router, step=step, message=message)
        return RouterPlanningSession(
            router=router,
            mappings=mappings,
            client=client,
            startup_failure=failure_detail,
        )

    def _service_failure_outcome(
        self,
        *,
        logger: RunLogger,
        router: RouterConfig,
        mapping: RouterServiceMappingConfig,
        step: RunStep,
        message: str,
        object_group_name: str | None = None,
        router_break_on_transport: bool = False,
    ) -> ServicePlanningOutcome:
        failure_detail = self._failure_detail(step=step, message=message)
        service_result = build_failed_service_result(
            mapping=mapping,
            failure_detail=failure_detail,
            object_group_name=object_group_name,
        )
        logger.event(
            "service_failed",
            step=step,
            router_id=router.id,
            service_key=mapping.service_key,
            object_group_name=object_group_name or mapping.object_group_name,
            status="failed",
            message=message,
        )
        return ServicePlanningOutcome(
            mapping=mapping,
            failed_service_result=service_result,
            router_failure_detail=(
                failure_detail
                if router_break_on_transport and failure_detail.category is not None
                else None
            ),
        )

    def _failure_detail(self, *, step: RunStep, message: str) -> FailureDetail:
        return build_classified_failure_detail(
            step=step,
            message=message,
            occurred_at=self._failure_time_provider(),
        )


def build_failed_router_result(
    *,
    router: RouterConfig,
    mappings: Sequence[RouterServiceMappingConfig],
    failure_detail: FailureDetail,
) -> RouterRunResult:
    service_results = [
        build_failed_service_result(
            mapping=mapping,
            failure_detail=failure_detail,
        )
        for mapping in mappings
    ]
    return RouterRunResult(
        router_id=router.id,
        status=aggregate_router_status(service_results),
        service_results=service_results,
        error_message=failure_detail.message,
        failure_detail=failure_detail,
    )


def build_service_result_from_plan(
    *,
    plan: ServiceSyncPlan,
    status: ServiceResultStatus | None = None,
) -> ServiceRunResult:
    diff = plan.object_group_diff
    static_diff = plan.static_route_diff
    return ServiceRunResult(
        service_key=plan.service_key,
        object_group_name=plan.object_group_name,
        status=(
            status
            if status is not None
            else ServiceResultStatus.UPDATED
            if plan.has_changes
            else ServiceResultStatus.NO_CHANGES
        ),
        added_count=len(diff.to_add) + (len(static_diff.to_add) if static_diff else 0),
        removed_count=len(diff.to_remove) + (len(static_diff.to_remove) if static_diff else 0),
        unchanged_count=len(diff.unchanged) + (len(static_diff.unchanged) if static_diff else 0),
        route_changed=plan.route_binding_diff.has_changes
        or (static_diff.has_changes if static_diff else False),
    )


def build_skipped_service_result(
    *,
    mapping: RouterServiceMappingConfig,
    failure_detail: FailureDetail,
    reason: str,
) -> ServiceRunResult:
    return ServiceRunResult(
        service_key=mapping.service_key,
        object_group_name=mapping.object_group_name,
        status=ServiceResultStatus.SKIPPED,
        error_message=f"Skipped after {reason}: {failure_detail.message}",
        failure_detail=failure_detail,
    )


def skipped_reason_for_failure(
    *,
    failure_detail: FailureDetail,
    non_transport_reason: str,
) -> str:
    if failure_detail.category is not None:
        return "router transport failure"
    return non_transport_reason


def log_service_skipped(
    *,
    logger: RunLogger,
    router: RouterConfig,
    mapping: RouterServiceMappingConfig,
    skipped_result: ServiceRunResult,
    failure_detail: FailureDetail,
) -> None:
    logger.event(
        "service_skipped",
        step=failure_detail.step,
        router_id=router.id,
        service_key=mapping.service_key,
        object_group_name=mapping.object_group_name,
        status=skipped_result.status.value,
        message=skipped_result.error_message,
    )


def log_router_failure(
    *,
    logger: RunLogger,
    router: RouterConfig,
    step: RunStep,
    message: str,
) -> None:
    logger.event(
        "router_failed",
        step=step,
        router_id=router.id,
        status="failed",
        message=message,
    )


def build_classified_failure_detail(
    *,
    step: RunStep,
    message: str,
    occurred_at: datetime | None = None,
) -> FailureDetail:
    return build_failure_detail(
        step=step,
        message=message,
        occurred_at=occurred_at or _utc_now(),
        category=classify_transport_failure(message),
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _NullRunLogger:
    @property
    def path(self) -> Path:
        return Path("data/logs/null.log")

    def event(
        self,
        event: str,
        *,
        step: RunStep | None = None,
        router_id: str | None = None,
        service_key: str | None = None,
        object_group_name: str | None = None,
        status: str | None = None,
        message: str | None = None,
    ) -> None:
        return None

    def close(self) -> None:
        return None


class NullLoggerFactory:
    def create(
        self,
        *,
        config: AppConfig,
        run_id: str,
        mode: RunMode,
        trigger: RunTrigger,
    ) -> RunLogger:
        del config, run_id, mode, trigger
        return _NullRunLogger()
