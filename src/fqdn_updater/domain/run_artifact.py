from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

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


class RunStep(StrEnum):
    SOURCE_LOAD = "source_load"
    SECRET_RESOLVE = "secret_resolve"
    CLIENT_CREATE = "client_create"
    READ_OBJECT_GROUP = "read_object_group"
    READ_ROUTE_BINDING = "read_route_binding"
    READ_DNS_PROXY_STATUS = "read_dns_proxy_status"
    PLAN_SERVICE = "plan_service"
    ENSURE_OBJECT_GROUP = "ensure_object_group"
    REMOVE_OBJECT_GROUP = "remove_object_group"
    REMOVE_ENTRIES = "remove_entries"
    ADD_ENTRIES = "add_entries"
    ENSURE_ROUTE = "ensure_route"
    REMOVE_ROUTE = "remove_route"
    SAVE_CONFIG = "save_config"


class FailureDetail(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    step: RunStep
    message: str
    occurred_at: datetime


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
    failure_detail: FailureDetail | None = None


class RouterRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    router_id: str
    status: RouterResultStatus
    service_results: list[ServiceRunResult] = Field(default_factory=list)
    error_message: str | None = None
    failure_detail: FailureDetail | None = None


class RunArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    trigger: RunTrigger
    mode: RunMode
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    log_path: Path
    router_results: list[RouterRunResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_timestamps(self) -> RunArtifact:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must be greater than or equal to started_at")
        return self
