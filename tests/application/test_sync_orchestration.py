from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fqdn_updater.application.service_sync_planning import ServiceSyncPlanner
from fqdn_updater.application.sync_orchestration import SyncOrchestrator
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig
from fqdn_updater.domain.keenetic import ObjectGroupState, RouteBindingState
from fqdn_updater.domain.run_artifact import (
    RouterResultStatus,
    RunStatus,
    RunTrigger,
    ServiceResultStatus,
)
from fqdn_updater.domain.source_loading import (
    NormalizedServiceSource,
    ServiceSourceFailure,
    SourceLoadReport,
)
from fqdn_updater.domain.static_route_diff import StaticRouteSpec, StaticRouteState


def test_sync_orchestrator_applies_changes_and_saves_once_per_router() -> None:
    config = _config()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(
                NormalizedServiceSource(
                    service_key="telegram",
                    entries=("new.example",),
                ),
            ),
        )
    )
    client_factory = RecordingClientFactory(
        states={
            ("router-1", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=(),
                exists=False,
            )
        },
        route_bindings={
            ("router-1", "svc-telegram"): RouteBindingState(
                object_group_name="svc-telegram",
                exists=False,
            )
        },
    )
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            ]
        ),
        run_id_factory=lambda: "run-101",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.MANUAL)

    client = client_factory.clients["router-1"]
    assert client.read_calls == [
        "svc-telegram",
        "svc-telegram-2",
        "svc-telegram-3",
        "svc-telegram-4",
        "route:svc-telegram",
        "route:svc-telegram-2",
        "route:svc-telegram-3",
        "route:svc-telegram-4",
    ]
    assert client.write_calls == [
        "ensure_object_group:svc-telegram",
        "add_entries:svc-telegram:new.example",
        "ensure_route:svc-telegram",
        "save_config",
    ]
    assert result.artifact.mode.value == "apply"
    assert result.artifact.status is RunStatus.SUCCESS
    assert result.artifact.router_results[0].status is RouterResultStatus.UPDATED
    assert (
        result.artifact.router_results[0].service_results[0].status is ServiceResultStatus.UPDATED
    )
    assert result.artifact.router_results[0].service_results[0].added_count == 1
    assert result.artifact.router_results[0].service_results[0].removed_count == 0


def test_sync_orchestrator_applies_sharded_large_service() -> None:
    config = _config()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(
                NormalizedServiceSource(
                    service_key="telegram",
                    entries=tuple(f"host-{index:03d}.example" for index in range(301)),
                ),
            ),
        )
    )
    client_factory = RecordingClientFactory(states={}, route_bindings={})
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            ]
        ),
        run_id_factory=lambda: "run-101-sharded",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.MANUAL)

    client = client_factory.clients["router-1"]
    assert client.read_calls == [
        "svc-telegram",
        "svc-telegram-2",
        "svc-telegram-3",
        "svc-telegram-4",
        "route:svc-telegram",
        "route:svc-telegram-2",
        "route:svc-telegram-3",
        "route:svc-telegram-4",
    ]
    assert client.write_calls[0] == "ensure_object_group:svc-telegram"
    assert client.write_calls[1].startswith("add_entries:svc-telegram:host-000.example")
    assert "host-299.example" in client.write_calls[1]
    assert "host-300.example" not in client.write_calls[1]
    assert client.write_calls[2] == "ensure_route:svc-telegram"
    assert client.write_calls[3] == "ensure_object_group:svc-telegram-2"
    assert client.write_calls[4] == "add_entries:svc-telegram-2:host-300.example"
    assert client.write_calls[5] == "ensure_route:svc-telegram-2"
    assert client.write_calls[6] == "save_config"
    assert [plan.object_group_name for plan in result.plans] == [
        "svc-telegram",
        "svc-telegram-2",
    ]
    assert [
        service.object_group_name for service in result.artifact.router_results[0].service_results
    ] == [
        "svc-telegram",
        "svc-telegram-2",
    ]


def test_sync_orchestrator_cleans_stale_shard_route_when_service_shrinks() -> None:
    config = _config()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(
                NormalizedServiceSource(
                    service_key="telegram",
                    entries=("keep.example",),
                ),
            ),
        )
    )
    client_factory = RecordingClientFactory(
        states={
            ("router-1", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=("keep.example",),
                exists=True,
            ),
            ("router-1", "svc-telegram-2"): ObjectGroupState(
                name="svc-telegram-2",
                entries=("old.example",),
                exists=True,
            ),
        },
        route_bindings={
            ("router-1", "svc-telegram"): RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Wireguard0",
                auto=True,
                exclusive=True,
            ),
            ("router-1", "svc-telegram-2"): RouteBindingState(
                object_group_name="svc-telegram-2",
                exists=True,
                route_target_type="interface",
                route_target_value="Wireguard0",
                auto=True,
                exclusive=True,
            ),
        },
    )
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            ]
        ),
        run_id_factory=lambda: "run-101-cleanup",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.MANUAL)

    client = client_factory.clients["router-1"]
    assert client.write_calls == [
        "remove_entries:svc-telegram-2:old.example",
        "remove_route:svc-telegram-2",
        "remove_object_group:svc-telegram-2",
        "save_config",
    ]
    service_results = result.artifact.router_results[0].service_results
    assert [service.status for service in service_results] == [
        ServiceResultStatus.NO_CHANGES,
        ServiceResultStatus.UPDATED,
    ]
    assert service_results[1].route_changed is True


def test_sync_orchestrator_skips_writes_and_save_when_diff_is_empty() -> None:
    config = _config()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(
                NormalizedServiceSource(
                    service_key="telegram",
                    entries=("keep.example",),
                ),
            ),
        )
    )
    client_factory = RecordingClientFactory(
        states={
            ("router-1", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=("keep.example",),
                exists=True,
            )
        },
        route_bindings={
            ("router-1", "svc-telegram"): RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Wireguard0",
                auto=True,
                exclusive=True,
            )
        },
    )
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            ]
        ),
        run_id_factory=lambda: "run-102",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.MANUAL)

    assert client_factory.clients["router-1"].write_calls == []
    assert result.artifact.status is RunStatus.SUCCESS
    assert result.artifact.router_results[0].status is RouterResultStatus.NO_CHANGES
    assert (
        result.artifact.router_results[0].service_results[0].status
        is ServiceResultStatus.NO_CHANGES
    )


def test_sync_orchestrator_applies_mixed_service_static_routes_and_saves_once() -> None:
    config = _config()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(
                NormalizedServiceSource(
                    service_key="telegram",
                    entries=("keep.example", "10.0.0.1/24", "2001:db8::1/64"),
                ),
            ),
        )
    )
    client_factory = RecordingClientFactory(
        states={
            ("router-1", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=("keep.example",),
                exists=True,
            )
        },
        route_bindings={
            ("router-1", "svc-telegram"): RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Wireguard0",
                auto=True,
                exclusive=True,
            )
        },
        static_routes={
            "router-1": (
                StaticRouteState(
                    network="10.0.0.0/24",
                    route_target_type="interface",
                    route_target_value="Wireguard0",
                    auto=True,
                    exclusive=True,
                    comment="fqdn-updater:telegram",
                ),
                StaticRouteState(
                    network="2001:db8:1::/64",
                    route_target_type="interface",
                    route_target_value="Wireguard0",
                    comment="fqdn-updater:telegram",
                ),
            )
        },
    )
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            ]
        ),
        run_id_factory=lambda: "run-107",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.MANUAL)

    client = client_factory.clients["router-1"]
    assert client.read_calls[-1] == "static_routes"
    assert client.write_calls == [
        "remove_static_route:2001:db8:1::/64",
        "ensure_static_route:2001:db8::/64",
        "save_config",
    ]
    service_result = result.artifact.router_results[0].service_results[0]
    assert service_result.status is ServiceResultStatus.UPDATED
    assert service_result.route_changed is True
    assert service_result.added_count == 1
    assert service_result.removed_count == 1


def test_sync_orchestrator_stops_current_router_after_write_failure_and_skips_remaining() -> None:
    config = _config_with_two_services_one_router()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(
                NormalizedServiceSource(service_key="telegram", entries=("new.example",)),
                NormalizedServiceSource(service_key="youtube", entries=("keep.example",)),
            ),
        )
    )
    client_factory = RecordingClientFactory(
        states={
            ("router-1", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=("old.example",),
                exists=True,
            ),
            ("router-1", "svc-youtube"): ObjectGroupState(
                name="svc-youtube",
                entries=("keep.example",),
                exists=True,
            ),
        },
        route_bindings={
            ("router-1", "svc-telegram"): RouteBindingState(
                object_group_name="svc-telegram",
                exists=False,
            ),
            ("router-1", "svc-youtube"): RouteBindingState(
                object_group_name="svc-youtube",
                exists=True,
                route_target_type="interface",
                route_target_value="Wireguard0",
                auto=True,
                exclusive=True,
            ),
        },
        write_errors={("router-1", "remove_entries", "svc-telegram"): "delete failed"},
    )
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            ]
        ),
        run_id_factory=lambda: "run-103",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.MANUAL)

    client = client_factory.clients["router-1"]
    assert client.write_calls == ["remove_entries:svc-telegram:old.example"]
    assert [service.status for service in result.artifact.router_results[0].service_results] == [
        ServiceResultStatus.FAILED,
        ServiceResultStatus.SKIPPED,
    ]
    assert "delete failed" in result.artifact.router_results[0].service_results[0].error_message
    assert "Skipped after router write failure" in (
        result.artifact.router_results[0].service_results[1].error_message or ""
    )
    assert result.artifact.router_results[0].status is RouterResultStatus.PARTIAL
    assert result.artifact.status is RunStatus.PARTIAL


def test_sync_orchestrator_continues_other_routers_after_partial_failures() -> None:
    config = _config_with_two_routers()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(NormalizedServiceSource(service_key="telegram", entries=("keep.example",)),),
            failed=(
                ServiceSourceFailure(
                    service_key="youtube",
                    source_url="https://example.com/youtube.lst",
                    message="timeout",
                ),
            ),
        )
    )
    client_factory = RecordingClientFactory(
        states={
            ("router-1", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=("keep.example",),
                exists=True,
            ),
            ("router-2", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=("old.example",),
                exists=True,
            ),
        },
        route_bindings={
            ("router-1", "svc-telegram"): RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Wireguard0",
                auto=True,
                exclusive=True,
            ),
            ("router-2", "svc-telegram"): RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Wireguard0",
                auto=True,
                exclusive=True,
            ),
        },
    )
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(
            passwords={"router-1": "secret-1", "router-2": "secret-2"}
        ),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            ]
        ),
        run_id_factory=lambda: "run-104",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.OPENCLAW)

    router_results = result.artifact.router_results
    assert [router.router_id for router in router_results] == ["router-1", "router-2"]
    assert router_results[0].status is RouterResultStatus.PARTIAL
    assert router_results[1].status is RouterResultStatus.UPDATED
    assert client_factory.clients["router-2"].write_calls == [
        "remove_entries:svc-telegram:old.example",
        "add_entries:svc-telegram:keep.example",
        "save_config",
    ]
    assert result.artifact.status is RunStatus.PARTIAL


def test_sync_orchestrator_marks_updated_services_failed_when_save_config_fails() -> None:
    config = _config()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(
                NormalizedServiceSource(
                    service_key="telegram",
                    entries=("new.example",),
                ),
            ),
        )
    )
    client_factory = RecordingClientFactory(
        states={
            ("router-1", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=("old.example",),
                exists=True,
            )
        },
        route_bindings={
            ("router-1", "svc-telegram"): RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Other0",
                auto=False,
                exclusive=False,
            )
        },
        save_errors={"router-1": "save failed"},
    )
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            ]
        ),
        run_id_factory=lambda: "run-105",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.SCHEDULED)

    router_result = result.artifact.router_results[0]
    assert client_factory.clients["router-1"].write_calls == [
        "remove_entries:svc-telegram:old.example",
        "add_entries:svc-telegram:new.example",
        "ensure_route:svc-telegram",
        "save_config",
    ]
    assert router_result.status is RouterResultStatus.FAILED
    assert router_result.service_results[0].status is ServiceResultStatus.FAILED
    assert "Save config failed after apply changes" in (
        router_result.service_results[0].error_message or ""
    )
    assert result.artifact.status is RunStatus.FAILED


def test_sync_orchestrator_applies_route_only_changes_and_saves() -> None:
    config = _config()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(
                NormalizedServiceSource(
                    service_key="telegram",
                    entries=("keep.example",),
                ),
            ),
        )
    )
    client_factory = RecordingClientFactory(
        states={
            ("router-1", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=("keep.example",),
                exists=True,
            )
        },
        route_bindings={
            ("router-1", "svc-telegram"): RouteBindingState(
                object_group_name="svc-telegram",
                exists=True,
                route_target_type="interface",
                route_target_value="Other0",
                auto=False,
                exclusive=False,
            )
        },
    )
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            ]
        ),
        run_id_factory=lambda: "run-106",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.MANUAL)

    client = client_factory.clients["router-1"]
    assert client.write_calls == [
        "ensure_route:svc-telegram",
        "save_config",
    ]
    service_result = result.artifact.router_results[0].service_results[0]
    assert service_result.status is ServiceResultStatus.UPDATED
    assert service_result.route_changed is True
    assert service_result.added_count == 0
    assert service_result.removed_count == 0


def test_sync_orchestrator_rejects_router_total_above_keenetic_fqdn_limit_before_writes() -> None:
    config = _config_with_four_services_one_router()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=tuple(
                NormalizedServiceSource(
                    service_key=f"service-{service_index}",
                    entries=tuple(
                        f"host-{service_index}-{entry_index:03d}.example"
                        for entry_index in range(260)
                    ),
                )
                for service_index in range(4)
            ),
        )
    )
    client_factory = RecordingClientFactory(states={}, route_bindings={})
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=timezone.utc),
                datetime(2026, 4, 9, 10, 1, tzinfo=timezone.utc),
            ]
        ),
        run_id_factory=lambda: "run-107",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.MANUAL)

    router_result = result.artifact.router_results[0]
    assert router_result.status is RouterResultStatus.FAILED
    assert "exceeding Keenetic total FQDN section limit 1024" in (router_result.error_message or "")
    assert [service.status for service in router_result.service_results] == [
        ServiceResultStatus.FAILED,
        ServiceResultStatus.FAILED,
        ServiceResultStatus.FAILED,
        ServiceResultStatus.FAILED,
    ]
    assert client_factory.clients["router-1"].write_calls == []


class StubSourceLoader:
    def __init__(self, report: SourceLoadReport) -> None:
        self.report = report

    def load_enabled_services(self, services: list[object]) -> SourceLoadReport:
        return self.report


class StubSecretResolver:
    def __init__(
        self,
        *,
        passwords: dict[str, str] | None = None,
        errors: dict[str, str] | None = None,
    ) -> None:
        self.passwords = passwords or {}
        self.errors = errors or {}

    def resolve(self, router: RouterConfig) -> str:
        if router.id in self.errors:
            raise RuntimeError(self.errors[router.id])
        return self.passwords[router.id]


class RecordingClientFactory:
    def __init__(
        self,
        *,
        states: dict[tuple[str, str], ObjectGroupState],
        route_bindings: dict[tuple[str, str], RouteBindingState],
        static_routes: dict[str, tuple[StaticRouteState, ...]] | None = None,
        read_errors: dict[tuple[str, str], str] | None = None,
        write_errors: dict[tuple[str, str, str], str] | None = None,
        save_errors: dict[str, str] | None = None,
    ) -> None:
        self.states = states
        self.route_bindings = route_bindings
        self.static_routes = static_routes or {}
        self.read_errors = read_errors or {}
        self.write_errors = write_errors or {}
        self.save_errors = save_errors or {}
        self.clients: dict[str, RecordingClient] = {}

    def create(self, router: RouterConfig, password: str) -> RecordingClient:
        client = RecordingClient(
            router_id=router.id,
            states=self.states,
            route_bindings=self.route_bindings,
            static_routes=self.static_routes.get(router.id, ()),
            read_errors=self.read_errors,
            write_errors=self.write_errors,
            save_errors=self.save_errors,
        )
        self.clients[router.id] = client
        return client


class RecordingClient:
    def __init__(
        self,
        *,
        router_id: str,
        states: dict[tuple[str, str], ObjectGroupState],
        route_bindings: dict[tuple[str, str], RouteBindingState],
        static_routes: tuple[StaticRouteState, ...],
        read_errors: dict[tuple[str, str], str],
        write_errors: dict[tuple[str, str, str], str],
        save_errors: dict[str, str],
    ) -> None:
        self.router_id = router_id
        self.states = states
        self.route_bindings = route_bindings
        self.static_routes = static_routes
        self.read_errors = read_errors
        self.write_errors = write_errors
        self.save_errors = save_errors
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []

    def get_object_group(self, name: str) -> ObjectGroupState:
        self.read_calls.append(name)
        key = (self.router_id, name)
        if key in self.read_errors:
            raise RuntimeError(self.read_errors[key])
        return self.states.get(key, ObjectGroupState(name=name, entries=(), exists=False))

    def get_route_binding(self, object_group_name: str) -> RouteBindingState:
        self.read_calls.append(f"route:{object_group_name}")
        key = (self.router_id, object_group_name)
        if key in self.read_errors:
            raise RuntimeError(self.read_errors[key])
        return self.route_bindings.get(
            key,
            RouteBindingState(object_group_name=object_group_name, exists=False),
        )

    def get_static_routes(self) -> tuple[StaticRouteState, ...]:
        self.read_calls.append("static_routes")
        return self.static_routes

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

    def save_config(self) -> None:
        self.write_calls.append("save_config")
        if self.router_id in self.save_errors:
            raise RuntimeError(self.save_errors[self.router_id])

    def get_dns_proxy_status(self):
        raise AssertionError("get_dns_proxy_status is not used in sync orchestration")

    def _raise_write_error(self, operation: str, name: str) -> None:
        key = (self.router_id, operation, name)
        if key in self.write_errors:
            raise RuntimeError(self.write_errors[key])


class RecordingArtifactWriter:
    def write(self, config: AppConfig, artifact) -> Path:
        return Path(config.runtime.artifacts_dir) / f"{artifact.run_id}.json"


class SequentialNowProvider:
    def __init__(self, values: list[datetime]) -> None:
        self._values = values
        self._index = 0

    def __call__(self) -> datetime:
        value = self._values[self._index]
        self._index += 1
        return value


def _config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_PASSWORD",
                    "enabled": True,
                },
            ],
            "services": [
                {
                    "key": "telegram",
                    "source_urls": ["https://example.com/telegram.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
            ],
            "mappings": [
                {
                    "router_id": "router-1",
                    "service_key": "telegram",
                    "object_group_name": "svc-telegram",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                },
            ],
        }
    )


def _config_with_two_services_one_router() -> AppConfig:
    return AppConfig.model_validate(
        {
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_PASSWORD",
                    "enabled": True,
                },
            ],
            "services": [
                {
                    "key": "telegram",
                    "source_urls": ["https://example.com/telegram.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
                {
                    "key": "youtube",
                    "source_urls": ["https://example.com/youtube.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
            ],
            "mappings": [
                {
                    "router_id": "router-1",
                    "service_key": "telegram",
                    "object_group_name": "svc-telegram",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                },
                {
                    "router_id": "router-1",
                    "service_key": "youtube",
                    "object_group_name": "svc-youtube",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                },
            ],
        }
    )


def _config_with_four_services_one_router() -> AppConfig:
    return AppConfig.model_validate(
        {
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_PASSWORD",
                    "enabled": True,
                },
            ],
            "services": [
                {
                    "key": f"service-{index}",
                    "source_urls": [f"https://example.com/service-{index}.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                }
                for index in range(4)
            ],
            "mappings": [
                {
                    "router_id": "router-1",
                    "service_key": f"service-{index}",
                    "object_group_name": f"svc-service-{index}",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                }
                for index in range(4)
            ],
        }
    )


def _config_with_two_routers() -> AppConfig:
    return AppConfig.model_validate(
        {
            "routers": [
                {
                    "id": "router-1",
                    "name": "Router 1",
                    "rci_url": "https://router-1.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_ONE_PASSWORD",
                    "enabled": True,
                },
                {
                    "id": "router-2",
                    "name": "Router 2",
                    "rci_url": "https://router-2.example/rci/",
                    "username": "api-user",
                    "password_env": "ROUTER_TWO_PASSWORD",
                    "enabled": True,
                },
            ],
            "services": [
                {
                    "key": "telegram",
                    "source_urls": ["https://example.com/telegram.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
                {
                    "key": "youtube",
                    "source_urls": ["https://example.com/youtube.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
            ],
            "mappings": [
                {
                    "router_id": "router-1",
                    "service_key": "telegram",
                    "object_group_name": "svc-telegram",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                },
                {
                    "router_id": "router-1",
                    "service_key": "youtube",
                    "object_group_name": "svc-youtube",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                },
                {
                    "router_id": "router-2",
                    "service_key": "telegram",
                    "object_group_name": "svc-telegram",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                },
            ],
        }
    )
