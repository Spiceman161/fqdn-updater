from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from fqdn_updater.application.keenetic_client import KeeneticClient
from fqdn_updater.application.run_planning import RunLogger, build_classified_failure_detail
from fqdn_updater.application.service_sync_planning import ServiceSyncPlan
from fqdn_updater.domain.config_schema import RouterConfig, RouterServiceMappingConfig
from fqdn_updater.domain.keenetic import RouteBindingSpec, RouteBindingState
from fqdn_updater.domain.run_artifact import FailureDetail, RunStep


class ServicePlanApplyService:
    def __init__(self, *, failure_time_provider: Callable[[], datetime] | None = None) -> None:
        self._failure_time_provider = failure_time_provider

    def apply_plan(
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
            current_binding = route_diff.current_binding
            current_matches_desired = (
                plan.desired_route_binding is not None
                and _route_binding_matches_desired(
                    current=current_binding,
                    desired=plan.desired_route_binding,
                )
            )
            bindings_to_remove = []
            if current_binding.exists and (
                plan.remove_route
                or plan.desired_route_binding is None
                or not current_matches_desired
            ):
                bindings_to_remove.append(current_binding)
            bindings_to_remove.extend(current_binding.duplicate_bindings)

            for binding in bindings_to_remove:
                try:
                    client.remove_route(binding)
                except Exception as exc:
                    return self._write_failure(
                        step=RunStep.REMOVE_ROUTE,
                        mapping=mapping,
                        exc=exc,
                    )

            if (
                not plan.remove_route
                and plan.desired_route_binding is not None
                and not current_matches_desired
            ):
                try:
                    client.ensure_route(plan.desired_route_binding)
                except Exception as exc:
                    return self._write_failure(
                        step=RunStep.ENSURE_ROUTE,
                        mapping=mapping,
                        exc=exc,
                    )

            if plan.remove_route and not bindings_to_remove:
                return self._write_failure(
                    step=RunStep.REMOVE_ROUTE,
                    mapping=mapping,
                    exc=RuntimeError("route binding is missing"),
                )

        if plan.static_route_diff is not None:
            for route in plan.static_route_diff.to_remove:
                try:
                    client.remove_static_route(route)
                except Exception as exc:
                    return self._write_failure(
                        step=RunStep.REMOVE_STATIC_ROUTE,
                        mapping=mapping,
                        exc=exc,
                    )

            for route in plan.static_route_diff.to_add:
                try:
                    client.ensure_static_route(route)
                except Exception as exc:
                    return self._write_failure(
                        step=RunStep.ENSURE_STATIC_ROUTE,
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
        return build_classified_failure_detail(
            step=step,
            message=f"Write stage failed for service '{mapping.service_key}': {exc}",
            occurred_at=self._failure_time_provider() if self._failure_time_provider else None,
        )


def _route_binding_matches_desired(
    *,
    current: RouteBindingState,
    desired: RouteBindingSpec,
) -> bool:
    return (
        current.exists
        and current.route_target_type == desired.route_target_type
        and current.route_target_value == desired.route_target_value
        and current.route_interface == desired.route_interface
        and current.auto == desired.auto
        and current.exclusive == desired.exclusive
    )
