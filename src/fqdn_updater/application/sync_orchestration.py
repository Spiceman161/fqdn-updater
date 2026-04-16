from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from fqdn_updater.application.keenetic_client import KeeneticClient, KeeneticClientFactory
from fqdn_updater.application.run_support import (
    aggregate_router_status,
    aggregate_run_status,
    build_failed_service_result,
    build_failure_detail,
    eligible_mappings,
    group_source_failures,
    validate_router_desired_fqdn_total,
)
from fqdn_updater.application.service_sync_planning import ServiceSyncPlan, ServiceSyncPlanner
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


class SyncExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    artifact: RunArtifact
    artifact_path: Path
    plans: tuple[ServiceSyncPlan, ...] = Field(default_factory=tuple)


class SyncOrchestrator:
    def __init__(
        self,
        *,
        source_loader: SourceLoader,
        secret_resolver: RouterSecretResolver,
        client_factory: KeeneticClientFactory,
        planner: ServiceSyncPlanner,
        artifact_writer: RunArtifactWriter,
        logger_factory: LoggerFactory | None = None,
        now_provider: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._source_loader = source_loader
        self._secret_resolver = secret_resolver
        self._client_factory = client_factory
        self._planner = planner
        self._artifact_writer = artifact_writer
        self._logger_factory = logger_factory or _NullLoggerFactory()
        self._now_provider = now_provider or _utc_now
        self._run_id_factory = run_id_factory or _generate_run_id

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> SyncExecutionResult:
        run_id = self._run_id_factory()
        started_at = self._now_provider()
        logger = self._logger_factory.create(
            config=config,
            run_id=run_id,
            mode=RunMode.APPLY,
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
                )
                router_results.append(router_result)
                plans.extend(router_plans)

            artifact = RunArtifact(
                run_id=run_id,
                trigger=trigger,
                mode=RunMode.APPLY,
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

            return SyncExecutionResult(
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
    ) -> tuple[RouterRunResult, list[ServiceSyncPlan]]:
        service_results: list[ServiceRunResult] = []
        plans: list[ServiceSyncPlan] = []
        changed_service_indexes: list[int] = []
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

        router_failure_detail: FailureDetail | None = None

        for index, mapping in enumerate(mappings):
            if router_failure_detail is not None:
                skipped_result = _build_skipped_service_result(
                    mapping=mapping,
                    failure_detail=router_failure_detail,
                )
                service_results.append(skipped_result)
                logger.event(
                    "service_skipped",
                    step=router_failure_detail.step,
                    router_id=router.id,
                    service_key=mapping.service_key,
                    object_group_name=mapping.object_group_name,
                    status=skipped_result.status.value,
                    message=skipped_result.error_message,
                )
                continue

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

            try:
                mapping_plans = self._planner.plan_mapping(
                    mapping=mapping,
                    desired_entries=desired_entries,
                    actual_states=actual_states,
                    actual_route_bindings=actual_route_bindings,
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
                if not plan.has_changes:
                    service_result = _build_no_changes_service_result(plan=plan)
                    service_results.append(service_result)
                    logger.event(
                        "service_planned",
                        router_id=router.id,
                        service_key=mapping.service_key,
                        object_group_name=plan.object_group_name,
                        status=service_result.status.value,
                        message="no changes",
                    )
                    continue

                router_failure_detail = self._apply_plan(
                    logger=logger,
                    client=client,
                    router=router,
                    mapping=mapping,
                    plan=plan,
                )
                if router_failure_detail is not None:
                    service_results.append(
                        build_failed_service_result(
                            mapping=mapping,
                            failure_detail=router_failure_detail,
                            object_group_name=plan.object_group_name,
                        )
                    )
                    logger.event(
                        "service_failed",
                        step=router_failure_detail.step,
                        router_id=router.id,
                        service_key=mapping.service_key,
                        object_group_name=plan.object_group_name,
                        status="failed",
                        message=router_failure_detail.message,
                    )
                    for remaining_mapping in mappings[index + 1 :]:
                        skipped_result = _build_skipped_service_result(
                            mapping=remaining_mapping,
                            failure_detail=router_failure_detail,
                        )
                        service_results.append(skipped_result)
                        logger.event(
                            "service_skipped",
                            step=router_failure_detail.step,
                            router_id=router.id,
                            service_key=remaining_mapping.service_key,
                            object_group_name=remaining_mapping.object_group_name,
                            status=skipped_result.status.value,
                            message=skipped_result.error_message,
                        )
                    break

                changed_service_indexes.append(len(service_results))
                service_result = _build_updated_service_result(plan=plan)
                service_results.append(service_result)
                logger.event(
                    "service_applied",
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

            if router_failure_detail is not None:
                break

        router_error_message: str | None = (
            router_failure_detail.message if router_failure_detail else None
        )
        router_failure_for_result = router_failure_detail
        if changed_service_indexes and router_failure_detail is None:
            try:
                client.save_config()
                logger.event(
                    "router_saved",
                    step=RunStep.SAVE_CONFIG,
                    router_id=router.id,
                    status="saved",
                )
            except Exception as exc:
                save_failure_detail = self._failure_detail(
                    step=RunStep.SAVE_CONFIG,
                    message=f"Save config failed after apply changes: {exc}",
                )
                logger.event(
                    "router_failed",
                    step=save_failure_detail.step,
                    router_id=router.id,
                    status="failed",
                    message=save_failure_detail.message,
                )
                for result_index in changed_service_indexes:
                    current_result = service_results[result_index]
                    service_results[result_index] = current_result.model_copy(
                        update={
                            "status": ServiceResultStatus.FAILED,
                            "error_message": save_failure_detail.message,
                            "failure_detail": save_failure_detail,
                        }
                    )
                router_error_message = save_failure_detail.message
                router_failure_for_result = save_failure_detail

        router_status = aggregate_router_status(service_results)
        logger.event("router_finished", router_id=router.id, status=router_status.value)
        return (
            RouterRunResult(
                router_id=router.id,
                status=router_status,
                service_results=service_results,
                error_message=router_error_message,
                failure_detail=router_failure_for_result,
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
        logger.event(
            "router_failed",
            step=step,
            router_id=router.id,
            status="failed",
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
                status=aggregate_router_status(service_results),
                service_results=service_results,
                error_message=failure_detail.message,
                failure_detail=failure_detail,
            ),
            plans,
        )

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

    def _apply_plan(
        self,
        *,
        logger: RunLogger,
        client: KeeneticClient,
        router: RouterConfig,
        mapping: RouterServiceMappingConfig,
        plan: ServiceSyncPlan,
    ) -> FailureDetail | None:
        object_group_diff = plan.object_group_diff
        route_diff = plan.route_binding_diff

        if object_group_diff.needs_create:
            try:
                client.ensure_object_group(plan.object_group_name)
            except Exception as exc:
                return self._write_failure(
                    step=RunStep.ENSURE_OBJECT_GROUP,
                    mapping=mapping,
                    exc=exc,
                )

        if object_group_diff.to_remove:
            try:
                client.remove_entries(plan.object_group_name, object_group_diff.to_remove)
            except Exception as exc:
                return self._write_failure(
                    step=RunStep.REMOVE_ENTRIES,
                    mapping=mapping,
                    exc=exc,
                )

        if object_group_diff.to_add:
            try:
                client.add_entries(plan.object_group_name, object_group_diff.to_add)
            except Exception as exc:
                return self._write_failure(
                    step=RunStep.ADD_ENTRIES,
                    mapping=mapping,
                    exc=exc,
                )

        if route_diff.has_changes:
            try:
                if plan.remove_route:
                    client.remove_route(route_diff.current_binding)
                elif plan.desired_route_binding is not None:
                    client.ensure_route(plan.desired_route_binding)
            except Exception as exc:
                return self._write_failure(
                    step=RunStep.REMOVE_ROUTE if plan.remove_route else RunStep.ENSURE_ROUTE,
                    mapping=mapping,
                    exc=exc,
                )

        if plan.remove_object_group:
            try:
                client.remove_object_group(plan.object_group_name)
            except Exception as exc:
                return self._write_failure(
                    step=RunStep.REMOVE_OBJECT_GROUP,
                    mapping=mapping,
                    exc=exc,
                )

        logger.event(
            "service_write_completed",
            router_id=router.id,
            service_key=mapping.service_key,
            object_group_name=plan.object_group_name,
            status="applied",
        )
        return None

    def _write_failure(
        self,
        *,
        step: RunStep,
        mapping: RouterServiceMappingConfig,
        exc: Exception,
    ) -> FailureDetail:
        return self._failure_detail(
            step=step,
            message=f"Write stage failed for service '{mapping.service_key}': {exc}",
        )

    def _failure_detail(self, *, step: RunStep, message: str) -> FailureDetail:
        return build_failure_detail(
            step=step,
            message=message,
            occurred_at=_utc_now(),
        )


def _build_no_changes_service_result(plan: ServiceSyncPlan) -> ServiceRunResult:
    diff = plan.object_group_diff
    return ServiceRunResult(
        service_key=plan.service_key,
        object_group_name=plan.object_group_name,
        status=ServiceResultStatus.NO_CHANGES,
        added_count=len(diff.to_add),
        removed_count=len(diff.to_remove),
        unchanged_count=len(diff.unchanged),
        route_changed=False,
    )


def _build_updated_service_result(plan: ServiceSyncPlan) -> ServiceRunResult:
    diff = plan.object_group_diff
    return ServiceRunResult(
        service_key=plan.service_key,
        object_group_name=plan.object_group_name,
        status=ServiceResultStatus.UPDATED,
        added_count=len(diff.to_add),
        removed_count=len(diff.to_remove),
        unchanged_count=len(diff.unchanged),
        route_changed=plan.route_binding_diff.has_changes,
    )


def _build_skipped_service_result(
    *,
    mapping: RouterServiceMappingConfig,
    failure_detail: FailureDetail,
) -> ServiceRunResult:
    return ServiceRunResult(
        service_key=mapping.service_key,
        object_group_name=mapping.object_group_name,
        status=ServiceResultStatus.SKIPPED,
        error_message=f"Skipped after router write failure: {failure_detail.message}",
        failure_detail=failure_detail,
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
