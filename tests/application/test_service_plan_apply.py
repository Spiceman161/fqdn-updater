from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fqdn_updater.application.service_plan_apply import ServicePlanApplyService
from fqdn_updater.application.service_sync_planning import ServiceSyncPlan, ServiceSyncPlanner
from fqdn_updater.domain.config_schema import RouterConfig, RouterServiceMappingConfig
from fqdn_updater.domain.keenetic import ObjectGroupState, RouteBindingState
from fqdn_updater.domain.object_group_sharding import managed_shard_names
from fqdn_updater.domain.run_artifact import RunStep
from fqdn_updater.domain.static_route_diff import StaticRouteSpec, StaticRouteState


def test_apply_plan_preserves_write_order_for_route_and_static_changes() -> None:
    plan = _plan_with_route_and_static_changes()
    client = RecordingClient()
    logger = RecordingLogger()

    failure = ServicePlanApplyService().apply_plan(
        logger=logger,
        client=client,
        router=_router(),
        mapping=_mapping(),
        plan=plan,
    )

    assert failure is None
    assert client.write_calls == [
        "remove_entries:svc-telegram:old.example",
        "add_entries:svc-telegram:new.example",
        "ensure_route:svc-telegram",
        "remove_static_route:10.0.1.0/24",
        "ensure_static_route:10.0.0.0/24",
    ]
    assert logger.events[-1] == {
        "event": "service_write_completed",
        "step": None,
        "router_id": "router-1",
        "service_key": "telegram",
        "object_group_name": "svc-telegram",
        "status": "applied",
        "message": None,
    }


def test_apply_plan_ensures_missing_object_group_before_adding_entries() -> None:
    plan = _planner().plan_mapping(
        mapping=_mapping(),
        desired_entries=("new.example",),
        actual_states=_actual_states(
            ObjectGroupState(
                name="svc-telegram",
                entries=(),
                exists=False,
            )
        ),
        actual_route_bindings=_route_bindings(
            RouteBindingState(
                object_group_name="svc-telegram",
                exists=False,
            )
        ),
    )[0]
    client = RecordingClient()

    failure = ServicePlanApplyService().apply_plan(
        logger=RecordingLogger(),
        client=client,
        router=_router(),
        mapping=_mapping(),
        plan=plan,
    )

    assert failure is None
    assert client.write_calls == [
        "ensure_object_group:svc-telegram",
        "add_entries:svc-telegram:new.example",
        "ensure_route:svc-telegram",
    ]


def test_apply_plan_stops_on_first_write_failure() -> None:
    occurred_at = datetime(2026, 4, 10, 9, 30, tzinfo=timezone.utc)
    client = RecordingClient(write_errors={("remove_entries", "svc-telegram"): "delete failed"})
    logger = RecordingLogger()

    failure = ServicePlanApplyService(failure_time_provider=lambda: occurred_at).apply_plan(
        logger=logger,
        client=client,
        router=_router(),
        mapping=_mapping(),
        plan=_plan_with_route_and_static_changes(),
    )

    assert failure is not None
    assert failure.step is RunStep.REMOVE_ENTRIES
    assert failure.message == "Write stage failed for service 'telegram': delete failed"
    assert failure.occurred_at == occurred_at
    assert client.write_calls == ["remove_entries:svc-telegram:old.example"]
    assert logger.events == []


class RecordingClient:
    def __init__(self, *, write_errors: dict[tuple[str, str], str] | None = None) -> None:
        self.write_calls: list[str] = []
        self.write_errors = write_errors or {}

    def ensure_object_group(self, name: str) -> None:
        self.write_calls.append(f"ensure_object_group:{name}")
        self._raise_write_error("ensure_object_group", name)

    def remove_object_group(self, name: str) -> None:
        self.write_calls.append(f"remove_object_group:{name}")
        self._raise_write_error("remove_object_group", name)

    def add_entries(self, name: str, items: tuple[str, ...]) -> None:
        self.write_calls.append(f"add_entries:{name}:{','.join(items)}")
        self._raise_write_error("add_entries", name)

    def remove_entries(self, name: str, items: tuple[str, ...]) -> None:
        self.write_calls.append(f"remove_entries:{name}:{','.join(items)}")
        self._raise_write_error("remove_entries", name)

    def ensure_route(self, binding) -> None:
        self.write_calls.append(f"ensure_route:{binding.object_group_name}")
        self._raise_write_error("ensure_route", binding.object_group_name)

    def remove_route(self, binding) -> None:
        self.write_calls.append(f"remove_route:{binding.object_group_name}")
        self._raise_write_error("remove_route", binding.object_group_name)

    def ensure_static_route(self, route: StaticRouteSpec) -> None:
        self.write_calls.append(f"ensure_static_route:{route.network}")
        self._raise_write_error("ensure_static_route", route.network)

    def remove_static_route(self, route: StaticRouteState) -> None:
        self.write_calls.append(f"remove_static_route:{route.network}")
        self._raise_write_error("remove_static_route", route.network)

    def _raise_write_error(self, operation: str, name: str) -> None:
        key = (operation, name)
        if key in self.write_errors:
            raise RuntimeError(self.write_errors[key])


class RecordingLogger:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    @property
    def path(self) -> Path:
        return Path("data/logs/test.log")

    def event(
        self,
        event: str,
        *,
        step=None,
        router_id: str | None = None,
        service_key: str | None = None,
        object_group_name: str | None = None,
        status: str | None = None,
        message: str | None = None,
    ) -> None:
        self.events.append(
            {
                "event": event,
                "step": step,
                "router_id": router_id,
                "service_key": service_key,
                "object_group_name": object_group_name,
                "status": status,
                "message": message,
            }
        )

    def close(self) -> None:
        return None


def _plan_with_route_and_static_changes() -> ServiceSyncPlan:
    return _planner().plan_mapping(
        mapping=_mapping(),
        desired_entries=("keep.example", "new.example", "10.0.0.1/24"),
        actual_states=_actual_states(
            ObjectGroupState(
                name="svc-telegram",
                entries=("keep.example", "old.example"),
                exists=True,
            )
        ),
        actual_route_bindings=_route_bindings(
            RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Other0",
                auto=False,
                exclusive=False,
            )
        ),
        actual_static_routes=(
            StaticRouteState(
                network="10.0.1.0/24",
                route_target_type="interface",
                route_target_value="Wireguard0",
                comment="fqdn-updater:telegram",
            ),
        ),
    )[0]


def _planner() -> ServiceSyncPlanner:
    return ServiceSyncPlanner()


def _actual_states(first_state: ObjectGroupState) -> dict[str, ObjectGroupState]:
    return {
        object_group_name: first_state
        if object_group_name == first_state.name
        else ObjectGroupState(name=object_group_name, entries=(), exists=False)
        for object_group_name in managed_shard_names("svc-telegram")
    }


def _route_bindings(first_binding: RouteBindingState) -> dict[str, RouteBindingState]:
    return {
        object_group_name: first_binding
        if object_group_name == first_binding.object_group_name
        else RouteBindingState(object_group_name=object_group_name, exists=False)
        for object_group_name in managed_shard_names("svc-telegram")
    }


def _router() -> RouterConfig:
    return RouterConfig.model_validate(
        {
            "id": "router-1",
            "name": "Router 1",
            "rci_url": "https://router-1.example/rci/",
            "username": "api-user",
            "password_env": "ROUTER_ONE_PASSWORD",
            "enabled": True,
        }
    )


def _mapping() -> RouterServiceMappingConfig:
    return RouterServiceMappingConfig.model_validate(
        {
            "router_id": "router-1",
            "service_key": "telegram",
            "object_group_name": "svc-telegram",
            "route_target_type": "interface",
            "route_target_value": "Wireguard0",
            "managed": True,
        }
    )
