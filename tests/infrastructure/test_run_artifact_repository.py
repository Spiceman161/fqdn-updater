from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from fqdn_updater.domain.config_schema import AppConfig
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
from fqdn_updater.infrastructure.run_artifact_repository import RunArtifactRepository


def test_write_creates_artifact_file_with_expected_json(tmp_path) -> None:
    repository = RunArtifactRepository()
    config = AppConfig.model_validate(
        {
            "runtime": {
                "artifacts_dir": str(tmp_path / "artifacts"),
            }
        }
    )
    artifact = _artifact()

    written_path = repository.write(config=config, artifact=artifact)

    assert written_path == tmp_path / "artifacts" / "run-001.json"
    assert written_path.exists()
    assert json.loads(written_path.read_text(encoding="utf-8")) == artifact.model_dump(mode="json")


def test_write_cleans_up_temp_file_on_replace_failure(tmp_path, monkeypatch) -> None:
    repository = RunArtifactRepository()
    config = AppConfig.model_validate(
        {
            "runtime": {
                "artifacts_dir": str(tmp_path / "artifacts"),
            }
        }
    )

    def failing_replace(self: Path, target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(Path, "replace", failing_replace)

    with pytest.raises(RuntimeError, match="Failed to write run artifact"):
        repository.write(config=config, artifact=_artifact())

    assert not (tmp_path / "artifacts" / "run-001.json").exists()
    assert list((tmp_path / "artifacts").glob(".run-001.json.*.tmp")) == []


def _artifact() -> RunArtifact:
    return RunArtifact(
        run_id="run-001",
        trigger=RunTrigger.OPENCLAW,
        mode=RunMode.DRY_RUN,
        status=RunStatus.SUCCESS,
        started_at=datetime(2026, 4, 8, 13, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 8, 13, 1, tzinfo=UTC),
        router_results=[
            RouterRunResult(
                router_id="router-1",
                status=RouterResultStatus.NO_CHANGES,
                service_results=[
                    ServiceRunResult(
                        service_key="telegram",
                        object_group_name="svc-telegram",
                        status=ServiceResultStatus.NO_CHANGES,
                        unchanged_count=5,
                    )
                ],
            )
        ],
    )
