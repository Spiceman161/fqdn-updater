from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.run_artifact import RunMode, RunStep, RunTrigger


class RunLogger:
    def __init__(
        self,
        *,
        run_id: str,
        mode: RunMode,
        trigger: RunTrigger,
        log_format: str,
        path: Path,
        stream: TextIO | None = None,
    ) -> None:
        self._run_id = run_id
        self._mode = mode
        self._trigger = trigger
        self._log_format = log_format
        self._path = path
        self._stream = stream or sys.stderr

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self._path

    def event(
        self,
        event: str,
        *,
        step: RunStep | None = None,
        router_id: str | None = None,
        service_key: str | None = None,
        object_group_name: str | None = None,
        status: str | None = None,
        message: str | None = None,
    ) -> None:
        occurred_at = datetime.now(UTC)
        payload = {
            "timestamp": occurred_at.isoformat().replace("+00:00", "Z"),
            "run_id": self._run_id,
            "mode": self._mode.value,
            "trigger": self._trigger.value,
            "event": event,
            "step": step.value if step is not None else None,
            "router_id": router_id,
            "service_key": service_key,
            "object_group_name": object_group_name,
            "status": status,
            "message": message,
        }
        rendered = (
            self._render_json(payload=payload)
            if self._log_format == "json"
            else self._render_text(payload=payload)
        )
        self._stream.write(rendered)
        self._stream.flush()
        self._handle.write(rendered)
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()

    def _render_json(self, *, payload: dict[str, str | None]) -> str:
        return json.dumps(payload, sort_keys=True) + "\n"

    def _render_text(self, *, payload: dict[str, str | None]) -> str:
        fields = [
            payload["timestamp"],
            f"run_id={payload['run_id']}",
            f"mode={payload['mode']}",
            f"trigger={payload['trigger']}",
            f"event={payload['event']}",
        ]
        if payload["step"] is not None:
            fields.append(f"step={payload['step']}")
        if payload["router_id"] is not None:
            fields.append(f"router_id={payload['router_id']}")
        if payload["service_key"] is not None:
            fields.append(f"service_key={payload['service_key']}")
        if payload["object_group_name"] is not None:
            fields.append(f"group={payload['object_group_name']}")
        if payload["status"] is not None:
            fields.append(f"status={payload['status']}")
        if payload["message"] is not None:
            fields.append(f"message={payload['message']}")
        return " ".join(fields) + "\n"


class RunLoggerFactory:
    def create(
        self,
        *,
        config: AppConfig,
        run_id: str,
        mode: RunMode,
        trigger: RunTrigger,
        stream: TextIO | None = None,
    ) -> RunLogger:
        path = self.build_path(config=config, run_id=run_id)
        return RunLogger(
            run_id=run_id,
            mode=mode,
            trigger=trigger,
            log_format=config.runtime.log_format,
            path=path,
            stream=stream,
        )

    def build_path(self, *, config: AppConfig, run_id: str) -> Path:
        extension = "jsonl" if config.runtime.log_format == "json" else "log"
        return Path(config.runtime.logs_dir) / f"{run_id}.{extension}"
