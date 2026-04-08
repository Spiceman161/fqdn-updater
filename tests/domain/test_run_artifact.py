from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fqdn_updater.domain.run_artifact import (
    RouterResultStatus,
    RouterRunResult,
    RunArtifact,
    RunMode,
    RunStatus,
    RunTrigger,
    ServiceResultStatus,
    ServiceRunResult,
)


def test_run_artifact_model_dump_is_machine_readable_and_deterministic() -> None:
    artifact = RunArtifact(
        run_id="run-001",
        trigger=RunTrigger.MANUAL,
        mode=RunMode.DRY_RUN,
        status=RunStatus.PARTIAL,
        started_at=datetime(2026, 4, 8, 13, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 8, 13, 5, tzinfo=UTC),
        router_results=[
            RouterRunResult(
                router_id="router-1",
                status=RouterResultStatus.PARTIAL,
                service_results=[
                    ServiceRunResult(
                        service_key="telegram",
                        object_group_name="svc-telegram",
                        status=ServiceResultStatus.UPDATED,
                        added_count=2,
                        removed_count=1,
                        unchanged_count=4,
                    ),
                    ServiceRunResult(
                        service_key="youtube",
                        object_group_name="svc-youtube",
                        status=ServiceResultStatus.FAILED,
                        error_message="upstream timeout",
                    ),
                ],
            )
        ],
    )

    assert artifact.model_dump(mode="json") == {
        "run_id": "run-001",
        "trigger": "manual",
        "mode": "dry_run",
        "status": "partial",
        "started_at": "2026-04-08T13:00:00Z",
        "finished_at": "2026-04-08T13:05:00Z",
        "router_results": [
            {
                "router_id": "router-1",
                "status": "partial",
                "service_results": [
                    {
                        "service_key": "telegram",
                        "object_group_name": "svc-telegram",
                        "status": "updated",
                        "added_count": 2,
                        "removed_count": 1,
                        "unchanged_count": 4,
                        "error_message": None,
                    },
                    {
                        "service_key": "youtube",
                        "object_group_name": "svc-youtube",
                        "status": "failed",
                        "added_count": 0,
                        "removed_count": 0,
                        "unchanged_count": 0,
                        "error_message": "upstream timeout",
                    },
                ],
                "error_message": None,
            }
        ],
    }


def test_run_artifact_rejects_finished_at_before_started_at() -> None:
    with pytest.raises(
        ValidationError, match="finished_at must be greater than or equal to started_at"
    ):
        RunArtifact(
            run_id="run-001",
            trigger=RunTrigger.SCHEDULED,
            mode=RunMode.APPLY,
            status=RunStatus.FAILED,
            started_at=datetime(2026, 4, 8, 13, 5, tzinfo=UTC),
            finished_at=datetime(2026, 4, 8, 13, 0, tzinfo=UTC),
        )


def test_service_run_result_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        ServiceRunResult(
            service_key="telegram",
            object_group_name="svc-telegram",
            status=ServiceResultStatus.NO_CHANGES,
            added_count=-1,
        )
