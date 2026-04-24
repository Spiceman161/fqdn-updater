from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fqdn_updater.application.run_history import RunHistoryService
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


@dataclass(frozen=True)
class _StoredArtifact:
    path: Path
    artifact: RunArtifact


@dataclass(frozen=True)
class _Warning:
    path: Path
    message: str


@dataclass(frozen=True)
class _ListResult:
    artifacts: tuple[_StoredArtifact, ...]
    warnings: tuple[_Warning, ...]
    total_count: int


class _RecordingRepository:
    def __init__(self, result: _ListResult) -> None:
        self.result = result
        self.calls: list[tuple[Path, int, int]] = []

    def list_recent(
        self,
        *,
        artifacts_dir: Path,
        limit: int = 10,
        offset: int = 0,
    ) -> _ListResult:
        self.calls.append((artifacts_dir, limit, offset))
        return self.result


def test_run_history_service_resolves_relative_artifacts_dir_from_config_path(tmp_path) -> None:
    config_path = tmp_path / "configs" / "fqdn.json"
    artifacts_dir = config_path.parent / "data" / "artifacts"
    repository = _RecordingRepository(
        result=_ListResult(
            artifacts=(
                _StoredArtifact(
                    path=artifacts_dir / "run-001.json",
                    artifact=_artifact(run_id="run-001"),
                ),
            ),
            warnings=(
                _Warning(
                    path=artifacts_dir / "broken.json",
                    message="artifact JSON is invalid: expected value at line 1 column 1",
                ),
            ),
            total_count=1,
        )
    )
    service = RunHistoryService(repository=repository)
    config = AppConfig.model_validate(
        {
            "runtime": {
                "artifacts_dir": "data/artifacts",
                "logs_dir": "data/logs",
                "secrets_env_file": ".env.secrets",
            }
        }
    )

    result = service.list_recent(config=config, config_path=config_path, limit=7, offset=10)

    assert repository.calls == [(artifacts_dir, 7, 10)]
    assert result.artifacts_dir == artifacts_dir
    assert result.runs[0].path == artifacts_dir / "run-001.json"
    assert result.runs[0].artifact.run_id == "run-001"
    assert result.warnings[0].path == artifacts_dir / "broken.json"
    assert result.warnings[0].message.startswith("artifact JSON is invalid")
    assert result.total_count == 1


def _artifact(*, run_id: str) -> RunArtifact:
    return RunArtifact(
        run_id=run_id,
        trigger=RunTrigger.MANUAL,
        mode=RunMode.DRY_RUN,
        status=RunStatus.SUCCESS,
        started_at=datetime(2026, 4, 8, 13, 0, tzinfo=UTC),
        finished_at=datetime(2026, 4, 8, 13, 1, tzinfo=UTC),
        log_path=Path("data/logs") / f"{run_id}.log",
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
