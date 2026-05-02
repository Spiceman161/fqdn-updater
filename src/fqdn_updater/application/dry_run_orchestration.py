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
    build_failed_router_result,
    build_service_result_from_plan,
    build_skipped_service_result,
    log_service_skipped,
)
from fqdn_updater.application.run_support import aggregate_router_status, aggregate_run_status
from fqdn_updater.application.service_sync_planning import ServiceSyncPlan, ServiceSyncPlanner
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig, RouterServiceMappingConfig
from fqdn_updater.domain.run_artifact import (
    FailureDetail,
    RouterRunResult,
    RunArtifact,
    RunMode,
    RunTrigger,
    ServiceRunResult,
)


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
        self._planning = RunPlanningService(
            source_loader=source_loader,
            secret_resolver=secret_resolver,
            client_factory=client_factory,
            planner=planner,
        )
        self._artifact_writer = artifact_writer
        self._logger_factory = logger_factory or NullLoggerFactory()
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
        snapshot: RunPlanningSnapshot,
    ) -> tuple[RouterRunResult, list[ServiceSyncPlan]]:
        service_results: list[ServiceRunResult] = []
        plans: list[ServiceSyncPlan] = []

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

        router_failure_detail: FailureDetail | None = None

        for mapping in mappings:
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

            plans.extend(outcome.plans)
            for plan in outcome.plans:
                service_result = build_service_result_from_plan(plan=plan)
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
                error_message=(
                    router_failure_detail.message if router_failure_detail is not None else None
                ),
                failure_detail=router_failure_detail,
            ),
            plans,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _generate_run_id() -> str:
    return str(uuid4())
