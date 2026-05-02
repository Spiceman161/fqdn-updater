from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from fqdn_updater.application.keenetic_client import KeeneticClientFactory
from fqdn_updater.application.run_locking import NullRunLockManager, RunLockManager
from fqdn_updater.application.run_planning import (
    LoggerFactory,
    NullLoggerFactory,
    RouterSecretResolver,
    RunArtifactWriter,
    RunLogger,
    RunPlanningService,
    RunPlanningSnapshot,
    SourceLoader,
    build_classified_failure_detail,
    build_failed_router_result,
    build_service_result_from_plan,
    build_skipped_service_result,
    log_service_skipped,
    skipped_reason_for_failure,
)
from fqdn_updater.application.run_support import (
    aggregate_router_status,
    aggregate_run_status,
    build_failed_service_result,
)
from fqdn_updater.application.service_plan_apply import ServicePlanApplyService
from fqdn_updater.application.service_sync_planning import ServiceSyncPlan, ServiceSyncPlanner
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig, RouterServiceMappingConfig
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
        run_lock_manager: RunLockManager | None = None,
        now_provider: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._source_loader = source_loader
        self._planning = RunPlanningService(
            source_loader=source_loader,
            secret_resolver=secret_resolver,
            client_factory=client_factory,
            planner=planner,
        )
        self._apply_service = ServicePlanApplyService()
        self._artifact_writer = artifact_writer
        self._logger_factory = logger_factory or NullLoggerFactory()
        self._run_lock_manager = run_lock_manager or NullRunLockManager()
        self._now_provider = now_provider or _utc_now
        self._run_id_factory = run_id_factory or _generate_run_id

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> SyncExecutionResult:
        with self._run_lock_manager.acquire(config=config):
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
                snapshot = self._planning.prepare_run(config=config)
                router_results: list[RouterRunResult] = []
                plans: list[ServiceSyncPlan] = []

                for router in config.routers:
                    router_mappings = snapshot.mappings_for_router(router.id)
                    if not router_mappings:
                        continue

                    router_result, router_plans = self._run_router(
                        logger=logger,
                        router=router,
                        mappings=router_mappings,
                        snapshot=snapshot,
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
        snapshot: RunPlanningSnapshot,
    ) -> tuple[RouterRunResult, list[ServiceSyncPlan]]:
        service_results: list[ServiceRunResult] = []
        plans: list[ServiceSyncPlan] = []
        changed_service_indexes: list[int] = []

        session = self._planning.start_router(
            logger=logger,
            router=router,
            mappings=mappings,
            snapshot=snapshot,
        )
        if session.startup_failure is not None:
            return (
                build_failed_router_result(
                    router=router,
                    mappings=mappings,
                    failure_detail=session.startup_failure,
                ),
                plans,
            )
        if session.client is None:
            raise RuntimeError("router planning session is not ready")

        router_failure_detail: FailureDetail | None = None

        for index, mapping in enumerate(mappings):
            if router_failure_detail is not None:
                skipped_result = build_skipped_service_result(
                    mapping=mapping,
                    failure_detail=router_failure_detail,
                    reason="router transport failure",
                )
                service_results.append(skipped_result)
                log_service_skipped(
                    logger=logger,
                    router=router,
                    mapping=mapping,
                    skipped_result=skipped_result,
                    failure_detail=router_failure_detail,
                )
                continue

            outcome = self._planning.plan_mapping(
                logger=logger,
                session=session,
                snapshot=snapshot,
                mapping=mapping,
            )
            if outcome.failed_service_result is not None:
                service_results.append(outcome.failed_service_result)
                if outcome.router_failure_detail is not None:
                    router_failure_detail = outcome.router_failure_detail
                continue

            mapping_plans = outcome.plans
            plans.extend(mapping_plans)
            for plan in mapping_plans:
                if not plan.has_changes:
                    service_result = build_service_result_from_plan(
                        plan=plan,
                        status=ServiceResultStatus.NO_CHANGES,
                    )
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

                router_failure_detail = self._apply_service.apply_plan(
                    logger=logger,
                    client=session.client,
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
                        skipped_result = build_skipped_service_result(
                            mapping=remaining_mapping,
                            failure_detail=router_failure_detail,
                            reason=skipped_reason_for_failure(
                                failure_detail=router_failure_detail,
                                non_transport_reason="router write failure",
                            ),
                        )
                        service_results.append(skipped_result)
                        log_service_skipped(
                            logger=logger,
                            router=router,
                            mapping=remaining_mapping,
                            skipped_result=skipped_result,
                            failure_detail=router_failure_detail,
                        )
                    break

                changed_service_indexes.append(len(service_results))
                service_result = build_service_result_from_plan(
                    plan=plan,
                    status=ServiceResultStatus.UPDATED,
                )
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
                session.client.save_config()
                logger.event(
                    "router_saved",
                    step=RunStep.SAVE_CONFIG,
                    router_id=router.id,
                    status="saved",
                )
            except Exception as exc:
                save_failure_detail = build_classified_failure_detail(
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_run_id() -> str:
    return str(uuid4())
