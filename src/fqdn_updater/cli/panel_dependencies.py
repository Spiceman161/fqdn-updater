from __future__ import annotations

from dataclasses import dataclass

from fqdn_updater.application.config_bootstrap import ConfigBootstrapService
from fqdn_updater.application.config_management import ConfigManagementService
from fqdn_updater.application.dry_run_orchestration import DryRunOrchestrator
from fqdn_updater.application.password_generation import RciPasswordGenerator
from fqdn_updater.application.route_target_discovery import RouteTargetDiscoveryService
from fqdn_updater.application.run_history import RunHistoryService
from fqdn_updater.application.service_sync_planning import ServiceSyncPlanner
from fqdn_updater.application.source_loading import SourceLoadingService
from fqdn_updater.application.status_diagnostics import StatusDiagnosticsService
from fqdn_updater.application.sync_orchestration import SyncOrchestrator
from fqdn_updater.infrastructure.config_repository import ConfigRepository
from fqdn_updater.infrastructure.keenetic_rci_client import KeeneticRciClientFactory
from fqdn_updater.infrastructure.raw_source_fetcher import HttpRawSourceFetcher
from fqdn_updater.infrastructure.router_secret_resolver import EnvironmentFileSecretResolver
from fqdn_updater.infrastructure.run_artifact_repository import RunArtifactRepository
from fqdn_updater.infrastructure.run_lock import FileRunLockManager
from fqdn_updater.infrastructure.run_logging import RunLoggerFactory
from fqdn_updater.infrastructure.service_count_cache import ServiceCountCacheRepository
from fqdn_updater.infrastructure.systemd_scheduler import SystemdScheduleInstaller


@dataclass(frozen=True)
class PanelDependencies:
    repository: ConfigRepository
    bootstrap_service: ConfigBootstrapService
    management_service: ConfigManagementService
    secret_resolver: EnvironmentFileSecretResolver
    client_factory: KeeneticRciClientFactory
    artifact_repository: RunArtifactRepository
    route_target_discovery_service: RouteTargetDiscoveryService
    run_history_service: RunHistoryService
    status_service: StatusDiagnosticsService
    schedule_installer: SystemdScheduleInstaller
    source_loading_service: SourceLoadingService
    service_count_cache_repository: ServiceCountCacheRepository
    dry_run_orchestrator: DryRunOrchestrator
    sync_orchestrator: SyncOrchestrator
    password_generator: RciPasswordGenerator


def build_panel_dependencies() -> PanelDependencies:
    repository = ConfigRepository()
    secret_resolver = EnvironmentFileSecretResolver()
    client_factory = KeeneticRciClientFactory()
    artifact_repository = RunArtifactRepository()
    source_loading_service = SourceLoadingService(fetcher=HttpRawSourceFetcher())

    return PanelDependencies(
        repository=repository,
        bootstrap_service=ConfigBootstrapService(repository=repository),
        management_service=ConfigManagementService(repository=repository),
        secret_resolver=secret_resolver,
        client_factory=client_factory,
        artifact_repository=artifact_repository,
        route_target_discovery_service=RouteTargetDiscoveryService(
            secret_resolver=secret_resolver,
            client_factory=client_factory,
        ),
        run_history_service=RunHistoryService(repository=artifact_repository),
        status_service=StatusDiagnosticsService(
            secret_resolver=secret_resolver,
            client_factory=client_factory,
        ),
        schedule_installer=SystemdScheduleInstaller(),
        source_loading_service=source_loading_service,
        service_count_cache_repository=ServiceCountCacheRepository(),
        dry_run_orchestrator=DryRunOrchestrator(
            source_loader=source_loading_service,
            secret_resolver=secret_resolver,
            client_factory=client_factory,
            planner=ServiceSyncPlanner(),
            artifact_writer=artifact_repository,
            logger_factory=RunLoggerFactory(),
            run_lock_manager=FileRunLockManager(),
        ),
        sync_orchestrator=SyncOrchestrator(
            source_loader=source_loading_service,
            secret_resolver=secret_resolver,
            client_factory=client_factory,
            planner=ServiceSyncPlanner(),
            artifact_writer=artifact_repository,
            logger_factory=RunLoggerFactory(),
            run_lock_manager=FileRunLockManager(),
        ),
        password_generator=RciPasswordGenerator(),
    )
