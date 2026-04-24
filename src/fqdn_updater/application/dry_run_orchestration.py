from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from fqdn_updater.application.keenetic_client import KeeneticClient, KeeneticClientFactory
from fqdn_updater.application.run_locking import NullRunLockManager, RunLockManager
from fqdn_updater.application.run_support import (
    aggregate_router_status,
    aggregate_run_status,
    build_failed_service_result,
    build_failure_detail,
    eligible_mappings,
    group_source_failures,
    has_static_route_entries,
    static_route_capable_service_keys,
    validate_router_desired_fqdn_total,
)
from fqdn_updater.application.service_sync_planning import ServiceSyncPlan, ServiceSyncPlanner
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig, RouterServiceMappingConfig
from fqdn_updater.domain.keenetic import ObjectGroupState, RouteBindingState
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.object_group_sharding import managed_shard_names
from fqdn_updater.domain.run_artifact import (
    FailureDetail,
    RouterResultStatus,
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


class DryRunExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    artifact: RunArtifact
    artifact_path: Path
    plans: tuple[ServiceSyncPlan, ...] = Field(default_factory=tuple)


class DryRunOrchestrator:
    def __init__(
        self,
        *,
        source_loader: SourceLoader,
        secret_resolver: RouterSecretResolver,
        client_factory: KeeneticClientFactory,
        planner: ServiceSyncPlanner,
        artifact_writer: RunArtifactWriter,
        logger_factory: LoggerFactory | None = None,
        run_lock_manager: RunLockManager | None = None,
        now_provider: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._source_loader = source_loader
        self._secret_resolver = secret_resolver
        self._client_factory = client_factory
        self._planner = planner
        self._artifact_writer = artifact_writer
        self._logger_factory = logger_factory or _NullLoggerFactory()
        self._run_lock_manager = run_lock_manager or NullRunLockManager()
        self._now_provider = now_provider or _utc_now
        self._run_id_factory = run_id_factory or _generate_run_id

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> DryRunExecutionResult:
        with self._run_lock_manager.acquire(config=config):
            run_id = self._run_id_factory()
            started_at = self._now_provider()
            logger = self._logger_factory.create(
                config=config,
                run_id=run_id,
                mode=RunMode.DRY_RUN,
                trigger=trigger,
            )
            logger.event("run_started", status="started")

            try:
                managed_mappings = eligible_mappings(config=config)
                loaded_sources = self._source_loader.load_enabled_services(config.services)
                desired_entries_by_service = {
                    source.service_key: source.typed_entries for source in loaded_sources.loaded
                }
                source_failures_by_service = group_source_failures(report=loaded_sources)
                static_route_service_keys = static_route_capable_service_keys(config)

                router_results: list[RouterRunResult] = []
                plans: list[ServiceSyncPlan] = []

                for router in config.routers:
                    router_mappings = [
                        mapping for mapping in managed_mappings if mapping.router_id == router.id
                    ]
                    if not router_mappings:
                        continue

                    router_result, router_plans = self._run_router(
                        logger=logger,
                        router=router,
                        mappings=router_mappings,
                        desired_entries_by_service=desired_entries_by_service,
                        source_failures_by_service=source_failures_by_service,
                        static_route_service_keys=static_route_service_keys,
                    )
                    router_results.append(router_result)
                    plans.extend(router_plans)

                artifact = RunArtifact(
                    run_id=run_id,
                    trigger=trigger,
                    mode=RunMode.DRY_RUN,
                    status=aggregate_run_status(router_results),
                    started_at=started_at,
                    finished_at=self._now_provider(),
                    log_path=logger.path,
                    router_results=router_results,
                )
                artifact_path = self._artifact_writer.write(config=config, artifact=artifact)
                logger.event(
                    "run_finished",
                    status=artifact.status.value,
                    message=f"artifact_path={artifact_path}",
                )

                return DryRunExecutionResult(
                    artifact=artifact,
                    artifact_path=artifact_path,
                    plans=tuple(plans),
                )
            finally:
                logger.close()

    def _run_router(
        self,
        *,
        logger: RunLogger,
        router: RouterConfig,
        mappings: Sequence[RouterServiceMappingConfig],
        desired_entries_by_service: dict[str, tuple[ObjectGroupEntry, ...]],
        source_failures_by_service: dict[str, str],
        static_route_service_keys: set[str],
    ) -> tuple[RouterRunResult, list[ServiceSyncPlan]]:
        service_results: list[ServiceRunResult] = []
        plans: list[ServiceSyncPlan] = []
        logger.event("router_started", router_id=router.id, status="started")

        try:
            password = self._secret_resolver.resolve(router)
        except Exception as exc:
            return self._failed_router_result(
                logger=logger,
                router=router,
                mappings=mappings,
                plans=plans,
                step=RunStep.SECRET_RESOLVE,
                message=str(exc),
            )

        try:
            client = self._client_factory.create(router=router, password=password)
        except Exception as exc:
            return self._failed_router_result(
                logger=logger,
                router=router,
                mappings=mappings,
                plans=plans,
                step=RunStep.CLIENT_CREATE,
                message=str(exc),
            )

        try:
            validate_router_desired_fqdn_total(
                router_id=router.id,
                mappings=mappings,
                desired_entries_by_service=desired_entries_by_service,
                source_failures_by_service=source_failures_by_service,
            )
        except Exception as exc:
            return self._failed_router_result(
                logger=logger,
                router=router,
                mappings=mappings,
                plans=plans,
                step=RunStep.PLAN_SERVICE,
                message=str(exc),
            )

        for mapping in mappings:
            failure_message = source_failures_by_service.get(mapping.service_key)
            if failure_message is not None:
                self._append_service_failure(
                    logger=logger,
                    router=router,
                    mapping=mapping,
                    step=RunStep.SOURCE_LOAD,
                    message=failure_message,
                    service_results=service_results,
                )
                continue

            desired_entries = desired_entries_by_service.get(mapping.service_key)
            if desired_entries is None:
                self._append_service_failure(
                    logger=logger,
                    router=router,
                    mapping=mapping,
                    step=RunStep.SOURCE_LOAD,
                    message=f"Missing loaded entries for service '{mapping.service_key}'",
                    service_results=service_results,
                )
                continue

            actual_states = self._read_object_group_shards(
                logger=logger,
                client=client,
                router=router,
                mapping=mapping,
                service_results=service_results,
            )
            if actual_states is None:
                continue

            actual_route_bindings = self._read_route_binding_shards(
                logger=logger,
                client=client,
                router=router,
                mapping=mapping,
                service_results=service_results,
            )
            if actual_route_bindings is None:
                continue

            actual_static_routes: tuple[StaticRouteState, ...] = ()
            if mapping.service_key in static_route_service_keys or has_static_route_entries(
                desired_entries
            ):
                maybe_actual_static_routes = self._read_static_routes(
                    logger=logger,
                    client=client,
                    router=router,
                    mapping=mapping,
                    service_results=service_results,
                )
                if maybe_actual_static_routes is None:
                    continue
                actual_static_routes = maybe_actual_static_routes

            try:
                mapping_plans = self._planner.plan_mapping(
                    mapping=mapping,
                    desired_entries=desired_entries,
                    actual_states=actual_states,
                    actual_route_bindings=actual_route_bindings,
                    actual_static_routes=actual_static_routes,
                )
            except Exception as exc:
                self._append_service_failure(
                    logger=logger,
                    router=router,
                    mapping=mapping,
                    step=RunStep.PLAN_SERVICE,
                    message=str(exc),
                    service_results=service_results,
                )
                continue

            plans.extend(mapping_plans)
            for plan in mapping_plans:
                service_result = _build_service_result_from_plan(plan=plan)
                service_results.append(service_result)
                logger.event(
                    "service_planned",
                    router_id=router.id,
                    service_key=mapping.service_key,
                    object_group_name=plan.object_group_name,
                    status=service_result.status.value,
                    message=(
                        f"added={service_result.added_count} "
                        f"removed={service_result.removed_count} "
                        f"route_changed={str(service_result.route_changed).lower()}"
                    ),
                )

        router_status = aggregate_router_status(service_results)
        logger.event("router_finished", router_id=router.id, status=router_status.value)
        return (
            RouterRunResult(
                router_id=router.id,
                status=router_status,
                service_results=service_results,
            ),
            plans,
        )

    def _failed_router_result(
        self,
        *,
        logger: RunLogger,
        router: RouterConfig,
        mappings: Sequence[RouterServiceMappingConfig],
        plans: list[ServiceSyncPlan],
        step: RunStep,
        message: str,
    ) -> tuple[RouterRunResult, list[ServiceSyncPlan]]:
        failure_detail = self._failure_detail(step=step, message=message)
        self._log_router_failure(
            logger=logger,
            router=router,
            step=step,
            message=message,
        )
        service_results = [
            build_failed_service_result(
                mapping=mapping,
                failure_detail=failure_detail,
            )
            for mapping in mappings
        ]
        return (
            RouterRunResult(
                router_id=router.id,
                status=RouterResultStatus.FAILED,
                service_results=service_results,
                error_message=failure_detail.message,
                failure_detail=failure_detail,
            ),
            plans,
        )

    def _read_object_group_shards(
        self,
        *,
        logger: RunLogger,
        client: KeeneticClient,
        router: RouterConfig,
        mapping: RouterServiceMappingConfig,
        service_results: list[ServiceRunResult],
    ) -> dict[str, ObjectGroupState] | None:
        actual_states: dict[str, ObjectGroupState] = {}
        for object_group_name in managed_shard_names(mapping.object_group_name):
            try:
                actual_states[object_group_name] = client.get_object_group(object_group_name)
            except Exception as exc:
                self._append_service_failure(
                    logger=logger,
                    router=router,
                    mapping=mapping,
                    step=RunStep.READ_OBJECT_GROUP,
                    message=str(exc),
                    service_results=service_results,
                    object_group_name=object_group_name,
                )
                return None
        return actual_states

    def _read_route_binding_shards(
        self,
        *,
        logger: RunLogger,
        client: KeeneticClient,
        router: RouterConfig,
        mapping: RouterServiceMappingConfig,
        service_results: list[ServiceRunResult],
    ) -> dict[str, RouteBindingState] | None:
        actual_route_bindings: dict[str, RouteBindingState] = {}
        for object_group_name in managed_shard_names(mapping.object_group_name):
            try:
                actual_route_bindings[object_group_name] = client.get_route_binding(
                    object_group_name
                )
            except Exception as exc:
                self._append_service_failure(
                    logger=logger,
                    router=router,
                    mapping=mapping,
                    step=RunStep.READ_ROUTE_BINDING,
                    message=str(exc),
                    service_results=service_results,
                    object_group_name=object_group_name,
                )
                return None
        return actual_route_bindings

    def _read_static_routes(
        self,
        *,
        logger: RunLogger,
        client: KeeneticClient,
        router: RouterConfig,
        mapping: RouterServiceMappingConfig,
        service_results: list[ServiceRunResult],
    ) -> tuple[StaticRouteState, ...] | None:
        try:
            return client.get_static_routes()
        except Exception as exc:
            self._append_service_failure(
                logger=logger,
                router=router,
                mapping=mapping,
                step=RunStep.READ_STATIC_ROUTES,
                message=str(exc),
                service_results=service_results,
            )
            return None

    def _append_service_failure(
        self,
        *,
        logger: RunLogger,
        router: RouterConfig,
        mapping: RouterServiceMappingConfig,
        step: RunStep,
        message: str,
        service_results: list[ServiceRunResult],
        object_group_name: str | None = None,
    ) -> None:
        failure_detail = self._failure_detail(step=step, message=message)
        service_results.append(
            build_failed_service_result(
                mapping=mapping,
                failure_detail=failure_detail,
                object_group_name=object_group_name,
            )
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

    def _log_router_failure(
        self,
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

    def _failure_detail(self, *, step: RunStep, message: str) -> FailureDetail:
        return build_failure_detail(
            step=step,
            message=message,
            occurred_at=_utc_now(),
        )


def _build_service_result_from_plan(plan: ServiceSyncPlan) -> ServiceRunResult:
    diff = plan.object_group_diff
    static_diff = plan.static_route_diff
    return ServiceRunResult(
        service_key=plan.service_key,
        object_group_name=plan.object_group_name,
        status=ServiceResultStatus.UPDATED if plan.has_changes else ServiceResultStatus.NO_CHANGES,
        added_count=len(diff.to_add) + (len(static_diff.to_add) if static_diff else 0),
        removed_count=len(diff.to_remove) + (len(static_diff.to_remove) if static_diff else 0),
        unchanged_count=len(diff.unchanged) + (len(static_diff.unchanged) if static_diff else 0),
        route_changed=plan.route_binding_diff.has_changes
        or (static_diff.has_changes if static_diff else False),
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _generate_run_id() -> str:
    return str(uuid4())


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


class _NullLoggerFactory:
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
