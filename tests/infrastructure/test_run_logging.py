from __future__ import annotations

import io
import json

from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.run_artifact import RunMode, RunStep, RunTrigger
from fqdn_updater.infrastructure.run_logging import RunLoggerFactory


def test_run_logger_factory_writes_text_log_to_stream_and_file(tmp_path) -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "artifacts_dir": str(tmp_path / "artifacts"),
                "logs_dir": str(tmp_path / "logs"),
                "log_format": "text",
            }
        }
    )
    stream = io.StringIO()

    logger = RunLoggerFactory().create(
        config=config,
        run_id="run-001",
        mode=RunMode.DRY_RUN,
        trigger=RunTrigger.MANUAL,
        stream=stream,
    )
    logger.event(
        "service_failed",
        step=RunStep.READ_OBJECT_GROUP,
        router_id="router-1",
        service_key="telegram",
        object_group_name="svc-telegram",
        status="failed",
        message="timeout",
    )
    logger.close()

    log_path = tmp_path / "logs" / "run-001.log"
    assert log_path.exists()
    assert "event=service_failed" in stream.getvalue()
    assert "step=read_object_group" in log_path.read_text(encoding="utf-8")


def test_run_logger_factory_writes_jsonl_log_to_stream_and_file(tmp_path) -> None:
    config = AppConfig.model_validate(
        {
            "runtime": {
                "artifacts_dir": str(tmp_path / "artifacts"),
                "logs_dir": str(tmp_path / "logs"),
                "log_format": "json",
            }
        }
    )
    stream = io.StringIO()

    logger = RunLoggerFactory().create(
        config=config,
        run_id="run-002",
        mode=RunMode.APPLY,
        trigger=RunTrigger.OPENCLAW,
        stream=stream,
    )
    logger.event("run_started", status="started")
    logger.close()

    log_path = tmp_path / "logs" / "run-002.jsonl"
    payload = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert json.loads(stream.getvalue().strip()) == payload
    assert payload["run_id"] == "run-002"
    assert payload["mode"] == "apply"
    assert payload["trigger"] == "openclaw"
    assert payload["event"] == "run_started"
    assert payload["status"] == "started"
