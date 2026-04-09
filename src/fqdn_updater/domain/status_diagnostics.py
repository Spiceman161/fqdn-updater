from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from fqdn_updater.domain.run_artifact import RunStep


class RouterDiagnosticStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


class OverallDiagnosticStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


class RouterStatusDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    router_id: str
    status: RouterDiagnosticStatus
    dns_proxy_enabled: bool | None = None
    error_message: str | None = None
    failure_step: RunStep | None = None


class StatusDiagnosticsResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config_ready: bool = True
    overall_status: OverallDiagnosticStatus
    checked_router_count: int = Field(ge=0)
    router_results: tuple[RouterStatusDiagnostic, ...] = Field(default_factory=tuple)
