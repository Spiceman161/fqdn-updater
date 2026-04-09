from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fqdn_updater.application.service_sync_planning import ServiceSyncPlanner
from fqdn_updater.application.sync_orchestration import SyncOrchestrator
from fqdn_updater.domain.config_schema import AppConfig, RouterConfig
from fqdn_updater.domain.keenetic import ObjectGroupState
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
        }
    )
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
                datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
            ]
        ),
        run_id_factory=lambda: "run-101",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.MANUAL)

    client = client_factory.clients["router-1"]
    assert client.write_calls == [
        "ensure_object_group:svc-telegram",
        "add_entries:svc-telegram:new.example",
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
        }
    )
    orchestrator = SyncOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
                datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
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
                datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
                datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
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
        }
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
                datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
                datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
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
                datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
                datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
            ]
        ),
        run_id_factory=lambda: "run-105",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.SCHEDULED)

    router_result = result.artifact.router_results[0]
    assert client_factory.clients["router-1"].write_calls == [
        "remove_entries:svc-telegram:old.example",
        "add_entries:svc-telegram:new.example",
        "save_config",
    ]
    assert router_result.status is RouterResultStatus.FAILED
    assert router_result.service_results[0].status is ServiceResultStatus.FAILED
    assert "Save config failed after apply changes" in (
        router_result.service_results[0].error_message or ""
    )
    assert result.artifact.status is RunStatus.FAILED


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
        read_errors: dict[tuple[str, str], str] | None = None,
        write_errors: dict[tuple[str, str, str], str] | None = None,
        save_errors: dict[str, str] | None = None,
    ) -> None:
        self.states = states
        self.read_errors = read_errors or {}
        self.write_errors = write_errors or {}
        self.save_errors = save_errors or {}
        self.clients: dict[str, RecordingClient] = {}

    def create(self, router: RouterConfig, password: str) -> RecordingClient:
        client = RecordingClient(
            router_id=router.id,
            states=self.states,
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
        read_errors: dict[tuple[str, str], str],
        write_errors: dict[tuple[str, str, str], str],
        save_errors: dict[str, str],
    ) -> None:
        self.router_id = router_id
        self.states = states
        self.read_errors = read_errors
        self.write_errors = write_errors
        self.save_errors = save_errors
        self.write_calls: list[str] = []

    def get_object_group(self, name: str) -> ObjectGroupState:
        key = (self.router_id, name)
        if key in self.read_errors:
            raise RuntimeError(self.read_errors[key])
        return self.states[key]

    def ensure_object_group(self, name: str) -> None:
        self.write_calls.append(f"ensure_object_group:{name}")
        self._raise_write_error("ensure_object_group", name)

    def add_entries(self, name: str, items: tuple[str, ...]) -> None:
        self.write_calls.append(f"add_entries:{name}:{','.join(items)}")
        self._raise_write_error("add_entries", name)

    def remove_entries(self, name: str, items: tuple[str, ...]) -> None:
        self.write_calls.append(f"remove_entries:{name}:{','.join(items)}")
        self._raise_write_error("remove_entries", name)

    def ensure_route(self, binding) -> None:
        raise AssertionError("ensure_route must not be called in slice S13")

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
