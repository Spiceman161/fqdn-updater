from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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
                "logs_dir": str(tmp_path / "logs"),
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
                "logs_dir": str(tmp_path / "logs"),
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


def test_list_recent_sorts_by_finished_at_then_run_id_then_filename_and_limits_results(
    tmp_path,
) -> None:
    repository = RunArtifactRepository()
    artifacts_dir = tmp_path / "artifacts"

    _write_artifact(
        artifacts_dir / "c.json",
        _artifact(run_id="run-c", finished_at=datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc)),
    )
    _write_artifact(
        artifacts_dir / "b.json",
        _artifact(run_id="run-b", finished_at=datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc)),
    )
    _write_artifact(
        artifacts_dir / "a.json",
        _artifact(run_id="run-b", finished_at=datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc)),
    )
    _write_artifact(
        artifacts_dir / "d.json",
        _artifact(run_id="run-a", finished_at=datetime(2026, 4, 8, 12, 59, tzinfo=timezone.utc)),
    )

    result = repository.list_recent(artifacts_dir=artifacts_dir, limit=3)

    assert [item.path.name for item in result.artifacts] == ["c.json", "b.json", "a.json"]
    assert [item.artifact.run_id for item in result.artifacts] == ["run-c", "run-b", "run-b"]
    assert [item.artifact.finished_at for item in result.artifacts] == [
        datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc),
        datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc),
    ]
    assert result.total_count == 4


def test_list_recent_supports_offset_for_pagination(tmp_path) -> None:
    repository = RunArtifactRepository()
    artifacts_dir = tmp_path / "artifacts"

    _write_artifact(
        artifacts_dir / "d.json",
        _artifact(run_id="run-d", finished_at=datetime(2026, 4, 8, 13, 3, tzinfo=timezone.utc)),
    )
    _write_artifact(
        artifacts_dir / "c.json",
        _artifact(run_id="run-c", finished_at=datetime(2026, 4, 8, 13, 2, tzinfo=timezone.utc)),
    )
    _write_artifact(
        artifacts_dir / "b.json",
        _artifact(run_id="run-b", finished_at=datetime(2026, 4, 8, 13, 1, tzinfo=timezone.utc)),
    )
    _write_artifact(
        artifacts_dir / "a.json",
        _artifact(run_id="run-a", finished_at=datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc)),
    )

    result = repository.list_recent(artifacts_dir=artifacts_dir, limit=2, offset=1)

    assert [item.path.name for item in result.artifacts] == ["c.json", "b.json"]
    assert [item.artifact.run_id for item in result.artifacts] == ["run-c", "run-b"]
    assert result.total_count == 4


def test_list_recent_returns_empty_tuple_for_missing_directory(tmp_path) -> None:
    repository = RunArtifactRepository()

    result = repository.list_recent(artifacts_dir=tmp_path / "missing", limit=10)

    assert result.artifacts == ()
    assert result.warnings == ()
    assert result.total_count == 0


def test_list_recent_reports_invalid_artifacts_as_warnings_without_failing(tmp_path) -> None:
    repository = RunArtifactRepository()
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    _write_artifact(
        artifacts_dir / "valid.json",
        _artifact(run_id="run-valid", finished_at=datetime(2026, 4, 8, 13, 0, tzinfo=timezone.utc)),
    )
    (artifacts_dir / "broken.json").write_text("{not json}", encoding="utf-8")
    (artifacts_dir / "invalid.json").write_text(
        json.dumps(
            {
                "mode": "dry_run",
                "status": "success",
                "started_at": "2026-04-08T13:00:00Z",
                "finished_at": "2026-04-08T13:01:00Z",
                "log_path": "data/logs/run-invalid.log",
                "router_results": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = repository.list_recent(artifacts_dir=artifacts_dir, limit=10)

    assert [item.path.name for item in result.artifacts] == ["valid.json"]
    assert [warning.path.name for warning in result.warnings] == ["broken.json", "invalid.json"]
    assert "artifact JSON is invalid" in result.warnings[0].message
    assert "artifact schema is invalid" in result.warnings[1].message
    assert result.total_count == 1


def _artifact(
    *,
    run_id: str = "run-001",
    finished_at: datetime = datetime(2026, 4, 8, 13, 1, tzinfo=timezone.utc),
) -> RunArtifact:
    return RunArtifact(
        run_id=run_id,
        trigger=RunTrigger.OPENCLAW,
        mode=RunMode.DRY_RUN,
        status=RunStatus.SUCCESS,
        started_at=finished_at - timedelta(minutes=1),
        finished_at=finished_at,
        log_path=f"data/logs/{run_id}.log",
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


def _write_artifact(path: Path, artifact: RunArtifact) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(artifact.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
