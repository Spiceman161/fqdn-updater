from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fqdn_updater.application.dry_run_orchestration import DryRunOrchestrator
from fqdn_updater.application.service_sync_planning import ServiceSyncPlanner
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


def test_dry_run_orchestrator_builds_plans_and_artifact_deterministically() -> None:
    config = _config()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(
                NormalizedServiceSource(
                    service_key="telegram",
                    entries=("keep.example", "new.example"),
                ),
            ),
        )
    )
    client_factory = RecordingClientFactory(
        states={
            ("router-1", "svc-telegram"): ObjectGroupState(
                name="svc-telegram",
                entries=("keep.example", "old.example"),
                exists=True,
            )
        }
    )
    artifact_writer = RecordingArtifactWriter()
    orchestrator = DryRunOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=artifact_writer,
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 8, 13, 0, tzinfo=UTC),
                datetime(2026, 4, 8, 13, 5, tzinfo=UTC),
            ]
        ),
        run_id_factory=lambda: "run-001",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.MANUAL)

    assert source_loader.load_calls == 1
    assert client_factory.created_passwords == [("router-1", "secret-1")]
    assert client_factory.clients["router-1"].read_calls == ["svc-telegram"]
    assert artifact_writer.last_path == Path("data/artifacts/run-001.json")
    assert result.artifact_path == Path("data/artifacts/run-001.json")
    assert result.artifact.run_id == "run-001"
    assert result.artifact.status is RunStatus.SUCCESS
    assert [router.router_id for router in result.artifact.router_results] == ["router-1"]
    assert result.artifact.router_results[0].status is RouterResultStatus.UPDATED
    assert (
        result.artifact.router_results[0].service_results[0].status is ServiceResultStatus.UPDATED
    )
    assert result.artifact.router_results[0].service_results[0].added_count == 1
    assert result.artifact.router_results[0].service_results[0].removed_count == 1
    assert result.artifact.router_results[0].service_results[0].unchanged_count == 1
    assert len(result.plans) == 1
    assert result.plans[0].service_key == "telegram"
    assert result.plans[0].object_group_diff.to_add == ("new.example",)


def test_dry_run_orchestrator_skips_disabled_and_unmanaged_mappings() -> None:
    config = AppConfig.model_validate(
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
                    "enabled": False,
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
                    "enabled": False,
                },
                {
                    "key": "discord",
                    "source_urls": ["https://example.com/discord.lst"],
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
                    "object_group_name": "svc-router-2",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": True,
                },
                {
                    "router_id": "router-1",
                    "service_key": "discord",
                    "object_group_name": "svc-unmanaged",
                    "route_target_type": "interface",
                    "route_target_value": "Wireguard0",
                    "managed": False,
                },
            ],
        }
    )
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(NormalizedServiceSource(service_key="telegram", entries=("keep.example",)),),
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
    orchestrator = DryRunOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 8, 13, 0, tzinfo=UTC),
                datetime(2026, 4, 8, 13, 1, tzinfo=UTC),
            ]
        ),
        run_id_factory=lambda: "run-002",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.OPENCLAW)

    assert [router.router_id for router in result.artifact.router_results] == ["router-1"]
    assert [
        service.service_key for service in result.artifact.router_results[0].service_results
    ] == ["telegram"]
    assert result.artifact.router_results[0].status is RouterResultStatus.NO_CHANGES
    assert (
        result.artifact.router_results[0].service_results[0].status
        is ServiceResultStatus.NO_CHANGES
    )


def test_dry_run_orchestrator_marks_source_failures_per_service_and_keeps_other_services() -> None:
    config = _config_with_two_services()
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
            )
        }
    )
    orchestrator = DryRunOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 8, 13, 0, tzinfo=UTC),
                datetime(2026, 4, 8, 13, 1, tzinfo=UTC),
            ]
        ),
        run_id_factory=lambda: "run-003",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.MANUAL)

    service_results = result.artifact.router_results[0].service_results
    assert [service.service_key for service in service_results] == ["telegram", "youtube"]
    assert service_results[0].status is ServiceResultStatus.NO_CHANGES
    assert service_results[1].status is ServiceResultStatus.FAILED
    assert "timeout" in service_results[1].error_message
    assert client_factory.clients["router-1"].read_calls == ["svc-telegram"]
    assert result.artifact.router_results[0].status is RouterResultStatus.PARTIAL
    assert result.artifact.status is RunStatus.PARTIAL


def test_dry_run_orchestrator_marks_router_secret_failure_for_all_services() -> None:
    config = _config_with_two_services()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(
                NormalizedServiceSource(service_key="telegram", entries=("keep.example",)),
                NormalizedServiceSource(service_key="youtube", entries=("keep.example",)),
            ),
        )
    )
    orchestrator = DryRunOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(errors={"router-1": "missing secret"}),
        client_factory=RecordingClientFactory(states={}),
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 8, 13, 0, tzinfo=UTC),
                datetime(2026, 4, 8, 13, 1, tzinfo=UTC),
            ]
        ),
        run_id_factory=lambda: "run-004",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.SCHEDULED)

    router_result = result.artifact.router_results[0]
    assert router_result.status is RouterResultStatus.FAILED
    assert router_result.error_message == "missing secret"
    assert [service.status for service in router_result.service_results] == [
        ServiceResultStatus.FAILED,
        ServiceResultStatus.FAILED,
    ]
    assert result.artifact.status is RunStatus.FAILED


def test_dry_run_orchestrator_marks_read_failures_partial_and_preserves_order() -> None:
    config = _config_with_two_services()
    source_loader = StubSourceLoader(
        SourceLoadReport(
            loaded=(
                NormalizedServiceSource(service_key="telegram", entries=("keep.example",)),
                NormalizedServiceSource(service_key="youtube", entries=("new.example",)),
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
        errors={("router-1", "svc-youtube"): "read failed"},
    )
    orchestrator = DryRunOrchestrator(
        source_loader=source_loader,
        secret_resolver=StubSecretResolver(passwords={"router-1": "secret-1"}),
        client_factory=client_factory,
        planner=ServiceSyncPlanner(),
        artifact_writer=RecordingArtifactWriter(),
        now_provider=SequentialNowProvider(
            [
                datetime(2026, 4, 8, 13, 0, tzinfo=UTC),
                datetime(2026, 4, 8, 13, 1, tzinfo=UTC),
            ]
        ),
        run_id_factory=lambda: "run-005",
    )

    result = orchestrator.run(config=config, trigger=RunTrigger.OPENCLAW)

    router_result = result.artifact.router_results[0]
    assert [service.service_key for service in router_result.service_results] == [
        "telegram",
        "youtube",
    ]
    assert [service.status for service in router_result.service_results] == [
        ServiceResultStatus.NO_CHANGES,
        ServiceResultStatus.FAILED,
    ]
    assert router_result.status is RouterResultStatus.PARTIAL
    assert result.artifact.status is RunStatus.PARTIAL
    assert [plan.service_key for plan in result.plans] == ["telegram"]


class StubSourceLoader:
    def __init__(self, report: SourceLoadReport) -> None:
        self.report = report
        self.load_calls = 0

    def load_enabled_services(self, services: list[object]) -> SourceLoadReport:
        self.load_calls += 1
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
        errors: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self.states = states
        self.errors = errors or {}
        self.created_passwords: list[tuple[str, str]] = []
        self.clients: dict[str, RecordingClient] = {}

    def create(self, router: RouterConfig, password: str) -> RecordingClient:
        self.created_passwords.append((router.id, password))
        client = RecordingClient(
            router_id=router.id,
            states=self.states,
            errors=self.errors,
        )
        self.clients[router.id] = client
        return client


class RecordingClient:
    def __init__(
        self,
        *,
        router_id: str,
        states: dict[tuple[str, str], ObjectGroupState],
        errors: dict[tuple[str, str], str],
    ) -> None:
        self.router_id = router_id
        self.states = states
        self.errors = errors
        self.read_calls: list[str] = []

    def get_object_group(self, name: str) -> ObjectGroupState:
        self.read_calls.append(name)
        key = (self.router_id, name)
        if key in self.errors:
            raise RuntimeError(self.errors[key])
        return self.states[key]


class RecordingArtifactWriter:
    def __init__(self) -> None:
        self.last_artifact = None
        self.last_path = None

    def write(self, config: AppConfig, artifact) -> Path:
        self.last_artifact = artifact
        self.last_path = Path(config.runtime.artifacts_dir) / f"{artifact.run_id}.json"
        return self.last_path


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


def _config_with_two_services() -> AppConfig:
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
