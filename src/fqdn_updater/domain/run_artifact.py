from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunMode(StrEnum):
    DRY_RUN = "dry_run"
    APPLY = "apply"


class RunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    OPENCLAW = "openclaw"


class RunStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class RouterResultStatus(StrEnum):
    UPDATED = "updated"
    NO_CHANGES = "no_changes"
    FAILED = "failed"
    PARTIAL = "partial"


class ServiceResultStatus(StrEnum):
    UPDATED = "updated"
    NO_CHANGES = "no_changes"
    FAILED = "failed"
    SKIPPED = "skipped"


class ServiceRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_key: str
    object_group_name: str
    status: ServiceResultStatus
    added_count: int = Field(default=0, ge=0)
    removed_count: int = Field(default=0, ge=0)
    unchanged_count: int = Field(default=0, ge=0)
    route_changed: bool = False
    error_message: str | None = None


class RouterRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    router_id: str
    status: RouterResultStatus
    service_results: list[ServiceRunResult] = Field(default_factory=list)
    error_message: str | None = None


class RunArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    trigger: RunTrigger
    mode: RunMode
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    router_results: list[RouterRunResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_timestamps(self) -> RunArtifact:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be greater than or equal to started_at")
        return self
