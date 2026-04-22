from __future__ import annotations

import re
from io import StringIO
from typing import Any

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output

from fqdn_updater.cli.panel_prompts import (
    DEFAULT_CHECKBOX_FOOTER,
    DEFAULT_CONFIRM_FOOTER,
    DEFAULT_TEXT_FOOTER,
    PromptChoice,
    QuestionaryPromptAdapter,
    _build_checkbox_question,
    _build_confirm_question,
    _build_select_question,
    _build_text_question,
)

ANSI_PATTERN = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def test_select_escape_returns_none_and_renders_custom_footer() -> None:
    result, rendered = _run_question(
        lambda *, input, output: _build_select_question(
            message="Раздел панели",
            choices=[
                PromptChoice("Маршрутизаторы", "routers"),
                PromptChoice("Прогоны", "runs"),
            ],
            instruction="Стрелки выбирают, Enter открывает, Esc возвращает назад.",
            style=_style(),
            input=input,
            output=output,
        ),
        user_input="\x1b",
    )

    assert result is None
    assert "Раздел панели" in rendered
    assert _last_non_empty_line(rendered) == (
        "Стрелки выбирают, Enter открывает, Esc возвращает назад."
    )


def test_select_arrow_navigation_still_works_with_escape_binding() -> None:
    result, _rendered = _run_question(
        lambda *, input, output: _build_select_question(
            message="Раздел панели",
            choices=[
                PromptChoice("Маршрутизаторы", "routers"),
                PromptChoice("Прогоны", "runs"),
            ],
            style=_style(),
            input=input,
            output=output,
        ),
        user_input="\x1b[B\r",
    )

    assert result == "runs"


def test_checkbox_escape_returns_none_and_renders_footer() -> None:
    result, rendered = _run_question(
        lambda *, input, output: _build_checkbox_question(
            message="Сервисы",
            choices=[
                PromptChoice("Telegram", "telegram"),
                PromptChoice("YouTube", "youtube"),
            ],
            style=_style(),
            input=input,
            output=output,
        ),
        user_input="\x1b",
    )

    assert result is None
    assert "Сервисы" in rendered
    assert _last_non_empty_line(rendered) == DEFAULT_CHECKBOX_FOOTER


def test_text_escape_returns_none_and_keeps_field_instruction_with_footer() -> None:
    result, rendered = _run_question(
        lambda *, input, output: _build_text_question(
            message="Router ID",
            instruction="Используйте lowercase, цифры, дефисы и подчёркивания.",
            style=_style(),
            input=input,
            output=output,
        ),
        user_input="\x1b",
    )

    assert result is None
    assert "Router ID" in rendered
    assert "Используйте lowercase, цифры, дефисы и подчёркивания." in rendered
    assert _last_non_empty_line(rendered) == DEFAULT_TEXT_FOOTER


def test_confirm_escape_returns_none_and_renders_footer() -> None:
    result, rendered = _run_question(
        lambda *, input, output: _build_confirm_question(
            message="Сохранить?",
            style=_style(),
            input=input,
            output=output,
        ),
        user_input="\x1b",
    )

    assert result is None
    assert "Сохранить?" in rendered
    assert _last_non_empty_line(rendered) == DEFAULT_CONFIRM_FOOTER


def _run_question(
    builder: Any,
    *,
    user_input: str,
) -> tuple[Any, str]:
    stream = StringIO()
    output = Vt100_Output(
        stream,
        lambda: Size(rows=12, columns=100),
        term="xterm",
        enable_bell=False,
        enable_cpr=False,
    )

    with create_pipe_input() as input:
        question = builder(input=input, output=output)
        input.send_text(user_input)
        result = question.ask()

    return result, _normalize_rendered_text(stream.getvalue())


def _normalize_rendered_text(rendered: str) -> str:
    text = ANSI_PATTERN.sub("", rendered)
    return text.replace("\r", "")


def _last_non_empty_line(rendered: str) -> str:
    return next(
        line.strip()
        for line in reversed(rendered.splitlines())
        if line.strip()
    )


def _style():
    return QuestionaryPromptAdapter()._style
