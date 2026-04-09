from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from fqdn_updater.application.keenetic_client import KeeneticClientFactory
from fqdn_updater.application.run_support import (
    aggregate_router_status,
    aggregate_run_status,
    build_failed_service_result,
    eligible_mappings,
    group_source_failures,
)
from fqdn_updater.application.service_sync_planning import ServiceSyncPlan, ServiceSyncPlanner
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig, RouterServiceMappingConfig
from fqdn_updater.domain.run_artifact import (
    RouterResultStatus,
    RouterRunResult,
    RunArtifact,
    RunMode,
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
        now_provider: Callable[[], datetime] | None = None,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._source_loader = source_loader
        self._secret_resolver = secret_resolver
        self._client_factory = client_factory
        self._planner = planner
        self._artifact_writer = artifact_writer
        self._now_provider = now_provider or _utc_now
        self._run_id_factory = run_id_factory or _generate_run_id

    def run(self, *, config: AppConfig, trigger: RunTrigger) -> DryRunExecutionResult:
        started_at = self._now_provider()
        managed_mappings = eligible_mappings(config=config)
        loaded_sources = self._source_loader.load_enabled_services(config.services)
        desired_entries_by_service = {
            source.service_key: source.entries for source in loaded_sources.loaded
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
                router=router,
                mappings=router_mappings,
                desired_entries_by_service=desired_entries_by_service,
                source_failures_by_service=source_failures_by_service,
            )
            router_results.append(router_result)
            plans.extend(router_plans)

        artifact = RunArtifact(
            run_id=self._run_id_factory(),
            trigger=trigger,
            mode=RunMode.DRY_RUN,
            status=aggregate_run_status(router_results),
            started_at=started_at,
            finished_at=self._now_provider(),
            router_results=router_results,
        )
        artifact_path = self._artifact_writer.write(config=config, artifact=artifact)

        return DryRunExecutionResult(
            artifact=artifact,
            artifact_path=artifact_path,
            plans=tuple(plans),
        )

    def _run_router(
        self,
        *,
        router: RouterConfig,
        mappings: Sequence[RouterServiceMappingConfig],
        desired_entries_by_service: dict[str, tuple[str, ...]],
        source_failures_by_service: dict[str, str],
    ) -> tuple[RouterRunResult, list[ServiceSyncPlan]]:
        service_results: list[ServiceRunResult] = []
        plans: list[ServiceSyncPlan] = []

        try:
            password = self._secret_resolver.resolve(router)
            client = self._client_factory.create(router=router, password=password)
        except Exception as exc:
            error_message = str(exc)
            service_results.extend(
                build_failed_service_result(
                    mapping=mapping,
                    error_message=error_message,
                )
                for mapping in mappings
            )
            return (
                RouterRunResult(
                    router_id=router.id,
                    status=RouterResultStatus.FAILED,
                    service_results=service_results,
                    error_message=error_message,
                ),
                plans,
            )

        for mapping in mappings:
            source_error = source_failures_by_service.get(mapping.service_key)
            if source_error is not None:
                service_results.append(
                    build_failed_service_result(mapping=mapping, error_message=source_error)
                )
                continue

            desired_entries = desired_entries_by_service.get(mapping.service_key)
            if desired_entries is None:
                service_results.append(
                    build_failed_service_result(
                        mapping=mapping,
                        error_message=(
                            f"Missing loaded entries for service '{mapping.service_key}'"
                        ),
                    )
                )
                continue

            try:
                actual_state = client.get_object_group(mapping.object_group_name)
                actual_route_binding = client.get_route_binding(mapping.object_group_name)
                plan = self._planner.plan(
                    mapping=mapping,
                    desired_entries=desired_entries,
                    actual_state=actual_state,
                    actual_route_binding=actual_route_binding,
                )
            except Exception as exc:
                service_results.append(
                    build_failed_service_result(mapping=mapping, error_message=str(exc))
                )
                continue

            plans.append(plan)
            service_results.append(_build_service_result_from_plan(plan=plan))

        return (
            RouterRunResult(
                router_id=router.id,
                status=aggregate_router_status(service_results),
                service_results=service_results,
            ),
            plans,
        )


def _build_service_result_from_plan(plan: ServiceSyncPlan) -> ServiceRunResult:
    diff = plan.object_group_diff
    return ServiceRunResult(
        service_key=plan.service_key,
        object_group_name=plan.object_group_name,
        status=ServiceResultStatus.UPDATED if plan.has_changes else ServiceResultStatus.NO_CHANGES,
        added_count=len(diff.to_add),
        removed_count=len(diff.to_remove),
        unchanged_count=len(diff.unchanged),
        route_changed=plan.route_binding_diff.has_changes,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _generate_run_id() -> str:
    return str(uuid4())
