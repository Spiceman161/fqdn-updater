from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from rich.console import Console

from fqdn_updater.cli.panel import PanelController
from fqdn_updater.cli.panel_prompts import CheckboxTableMeta, PromptChoice
from fqdn_updater.domain.config_schema import AppConfig
from fqdn_updater.domain.object_group_entry import ObjectGroupEntry
from fqdn_updater.domain.source_loading import NormalizedServiceSource, SourceLoadReport


class ScriptedPromptAdapter:
    def __init__(
        self,
        *,
        select_answers: Iterable[str | None] = (),
        checkbox_answers: Iterable[list[str] | None] = (),
        text_answers: Iterable[str | None] = (),
        confirm_answers: Iterable[bool | None] = (),
    ) -> None:
        self._select_answers = deque(select_answers)
        self._checkbox_answers = deque(checkbox_answers)
        self._text_answers = deque(text_answers)
        self._confirm_answers = deque(confirm_answers)
        self.select_calls: list[dict[str, Any]] = []
        self.checkbox_calls: list[dict[str, Any]] = []
        self.text_calls: list[dict[str, Any]] = []
        self.confirm_calls: list[dict[str, Any]] = []
        self.history_select_calls: list[dict[str, Any]] = []
        self.pause_messages: list[str] = []

    def select(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        default: str | None = None,
        instruction: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> str | None:
        self.select_calls.append(
            {
                "message": message,
                "choices": [choice.value for choice in choices],
                "choice_titles": [choice.title for choice in choices],
                "default": default,
                "instruction": instruction,
                "hint_lines": hint_lines,
            }
        )
        return self._pop(self._select_answers, f"select:{message}")

    def checkbox(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        instruction: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
        table_meta: CheckboxTableMeta | None = None,
    ) -> list[str] | None:
        checked_values = tuple(choice.value for choice in choices if choice.checked)
        self.checkbox_calls.append(
            {
                "message": message,
                "choices": [
                    {
                        "title": choice.title,
                        "value": choice.value,
                        "checked": choice.checked,
                    }
                    for choice in choices
                ],
                "instruction": instruction,
                "hint_lines": hint_lines,
                "table_header": table_meta.header if table_meta is not None else None,
                "table_summary": (
                    _plain_table_summary(table_meta.summary(checked_values))
                    if table_meta is not None
                    else None
                ),
                "selection_groups": (
                    table_meta.selection_groups if table_meta is not None else None
                ),
            }
        )
        return self._pop(self._checkbox_answers, f"checkbox:{message}")

    def text(
        self,
        *,
        message: str,
        default: str = "",
        instruction: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> str | None:
        self.text_calls.append(
            {
                "message": message,
                "default": default,
                "instruction": instruction,
                "hint_lines": hint_lines,
            }
        )
        return self._pop(self._text_answers, f"text:{message}")

    def confirm(
        self,
        *,
        message: str,
        default: bool = True,
        instruction: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> bool | None:
        self.confirm_calls.append(
            {
                "message": message,
                "default": default,
                "instruction": instruction,
                "hint_lines": hint_lines,
            }
        )
        return self._pop(self._confirm_answers, f"confirm:{message}")

    def history_select(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        default: str | None = None,
        page_label: str,
        has_previous_page: bool,
        has_next_page: bool,
    ) -> str | None:
        self.history_select_calls.append(
            {
                "message": message,
                "choices": [choice.value for choice in choices],
                "choice_titles": [choice.title for choice in choices],
                "default": default,
                "page_label": page_label,
                "has_previous_page": has_previous_page,
                "has_next_page": has_next_page,
            }
        )
        return self._pop(self._select_answers, f"history_select:{message}")

    def pause(self, *, message: str, hint_lines: tuple[str, ...] | None = None) -> None:
        self.pause_messages.append(message)

    def assert_consumed(self) -> None:
        assert not self._select_answers
        assert not self._checkbox_answers
        assert not self._text_answers
        assert not self._confirm_answers

    @staticmethod
    def _pop(queue: deque[Any], label: str) -> Any:
        if not queue:
            raise AssertionError(f"Missing scripted prompt answer for {label}")
        return queue.popleft()


def _plain_table_summary(summary: str | list[tuple[str, str]]) -> str:
    if isinstance(summary, str):
        return summary
    return "".join(text for _style, text in summary)


class ScriptedSourceLoadingService:
    def __init__(self, report: SourceLoadReport) -> None:
        self.report = report
        self.calls: list[list[str]] = []

    def load_enabled_services(self, services: list[Any]) -> SourceLoadReport:
        self.calls.append([service.key for service in services])
        return self.report


def make_source_load_report(
    *,
    loaded: dict[str, tuple[ObjectGroupEntry, ...]],
) -> SourceLoadReport:
    return SourceLoadReport(
        loaded=tuple(
            NormalizedServiceSource(service_key=service_key, typed_entries=typed_entries)
            for service_key, typed_entries in loaded.items()
        )
    )


def make_empty_source_load_report() -> SourceLoadReport:
    return SourceLoadReport()


def make_panel_controller(
    tmp_path: Path, *, prompts: ScriptedPromptAdapter
) -> tuple[PanelController, Console]:
    console = Console(force_terminal=True, record=True, width=120)
    controller = PanelController(
        config_path=tmp_path / "config.json",
        console=console,
        prompts=prompts,
    )
    controller._source_loading_service = ScriptedSourceLoadingService(  # type: ignore[attr-defined]
        make_empty_source_load_report()
    )
    return controller, console


def write_config(
    path: Path,
    *,
    routers: list[dict[str, Any]] | None = None,
    services: list[dict[str, Any]] | None = None,
    mappings: list[dict[str, Any]] | None = None,
    runtime: dict[str, Any] | None = None,
) -> None:
    config = AppConfig.model_validate(
        {
            "routers": routers or [],
            "services": services
            or [
                {
                    "key": "telegram",
                    "source_urls": ["https://example.com/telegram.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
                {
                    "key": "google_ai",
                    "source_urls": ["https://example.com/google_ai.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
                {
                    "key": "youtube",
                    "source_urls": ["https://example.com/youtube.lst"],
                    "format": "raw_domain_list",
                    "enabled": True,
                },
            ],
            "mappings": mappings or [],
            "runtime": runtime or {"secrets_env_file": ".env.secrets"},
        }
    )
    path.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
