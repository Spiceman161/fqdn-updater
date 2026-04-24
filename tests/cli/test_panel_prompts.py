from __future__ import annotations

import re
from io import StringIO
from typing import Any

from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console

from fqdn_updater.cli.panel_prompts import (
    DEFAULT_CHECKBOX_FOOTER,
    DEFAULT_CONFIRM_FOOTER,
    DEFAULT_TEXT_FOOTER,
    CheckboxTableMeta,
    PromptChoice,
    QuestionaryPromptAdapter,
    _ask_and_echo,
    _build_checkbox_question,
    _build_confirm_question,
    _build_select_question,
    _build_text_question,
    _render_checkbox_answer,
)

ANSI_PATTERN = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def test_select_question_uses_custom_footer_and_does_not_leak_it_into_history() -> None:
    expected_footer = "Стрелки выбирают, Enter открывает, Esc возвращает назад."
    question = _build_select_question(
        message="Раздел панели",
        choices=[
            PromptChoice("Маршрутизаторы", "routers"),
            PromptChoice("Прогоны", "runs"),
        ],
        instruction=expected_footer,
        style=_style(),
    )
    assert _footer_text(question) == expected_footer

    result, history = _run_history_entry(
        lambda *, input, output: _build_select_question(
            message="Раздел панели",
            choices=[
                PromptChoice("Маршрутизаторы", "routers"),
                PromptChoice("Прогоны", "runs"),
            ],
            instruction=expected_footer,
            style=_style(),
            input=input,
            output=output,
        ),
        user_input="\r",
        message="Раздел панели",
        render_answer=lambda value: "Маршрутизаторы" if value == "routers" else None,
    )
    assert result == "routers"
    assert history == "> Раздел панели Маршрутизаторы\n"
    assert expected_footer not in history


def test_select_question_keeps_hint_panel_only_while_active() -> None:
    hint_lines = ("Введите имя нового маршрутизатора.",)

    result, rendered, _raw = _run_question(
        lambda *, input, output: _build_select_question(
            message="Раздел панели",
            choices=[
                PromptChoice("Маршрутизаторы", "routers"),
                PromptChoice("Прогоны", "runs"),
            ],
            hint_lines=hint_lines,
            style=_style(),
            input=input,
            output=output,
        ),
        user_input="\r",
    )
    assert result == "routers"
    assert "Подсказка" in rendered
    assert hint_lines[0] in rendered

    _result, history = _run_history_entry(
        lambda *, input, output: _build_select_question(
            message="Раздел панели",
            choices=[
                PromptChoice("Маршрутизаторы", "routers"),
                PromptChoice("Прогоны", "runs"),
            ],
            hint_lines=hint_lines,
            style=_style(),
            input=input,
            output=output,
        ),
        user_input="\r",
        message="Раздел панели",
        render_answer=lambda value: "Маршрутизаторы" if value == "routers" else None,
    )
    assert "Подсказка" not in history
    assert hint_lines[0] not in history


def test_select_arrow_navigation_still_works_with_escape_binding() -> None:
    result, _rendered, _raw = _run_question(
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


def test_checkbox_question_keeps_footer_only_while_active() -> None:
    question = _build_checkbox_question(
        message="Сервисы",
        choices=[
            PromptChoice("Telegram", "telegram"),
            PromptChoice("YouTube", "youtube"),
        ],
        style=_style(),
    )
    assert _footer_text(question) == DEFAULT_CHECKBOX_FOOTER

    result, history = _run_history_entry(
        lambda *, input, output: _build_checkbox_question(
            message="Сервисы",
            choices=[
                PromptChoice("Telegram", "telegram", checked=True),
                PromptChoice("YouTube", "youtube"),
            ],
            style=_style(),
            input=input,
            output=output,
        ),
        user_input="\r",
        message="Сервисы",
        render_answer=lambda value: "Telegram" if value == ["telegram"] else None,
    )
    assert result == ["telegram"]
    assert history == "> Сервисы Telegram\n"
    assert DEFAULT_CHECKBOX_FOOTER not in history

    _result, _rendered, raw = _run_question(
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
    assert "\x1b[7m" not in raw
    assert "\x1b[0;38;5;76;7m" not in raw


def test_checkbox_question_renders_table_header_and_live_summary_only_while_active() -> None:
    table_meta = CheckboxTableMeta(
        header="Сервис                 | домены |   IPv4 |   IPv6",
        summary=lambda selected: f"Итого выбрано         | {len(selected):>7} | {0:>7} | {0:>7}",
    )

    result, rendered, _raw = _run_question(
        lambda *, input, output: _build_checkbox_question(
            message="Сервисы",
            choices=[
                PromptChoice("Telegram", "telegram"),
                PromptChoice("YouTube", "youtube"),
            ],
            table_meta=table_meta,
            style=_style(),
            input=input,
            output=output,
        ),
        user_input=" \r",
    )
    assert result == ["telegram"]
    assert table_meta.header in rendered
    assert "Итого выбрано" in rendered

    _result, history = _run_history_entry(
        lambda *, input, output: _build_checkbox_question(
            message="Сервисы",
            choices=[
                PromptChoice("Telegram", "telegram"),
                PromptChoice("YouTube", "youtube"),
            ],
            table_meta=table_meta,
            style=_style(),
            input=input,
            output=output,
        ),
        user_input=" \r",
        message="Сервисы",
        render_answer=lambda value: "Telegram" if value == ["telegram"] else None,
    )
    assert table_meta.header not in history
    assert "Итого выбрано" not in history


def test_render_checkbox_answer_uses_display_titles_for_table_mode() -> None:
    assert (
        _render_checkbox_answer(
            result=["meta", "youtube"],
            choice_titles={
                "meta": "meta (whatsapp)",
                "youtube": "youtube",
            },
        )
        == "meta (whatsapp), youtube"
    )


def test_text_question_moves_field_instruction_to_footer_only() -> None:
    question = _build_text_question(
        message="Router ID",
        instruction="Используйте lowercase, цифры, дефисы и подчёркивания.",
        style=_style(),
    )
    assert _footer_text(question) == (
        f"Используйте lowercase, цифры, дефисы и подчёркивания. {DEFAULT_TEXT_FOOTER}"
    )

    result, history = _run_history_entry(
        lambda *, input, output: _build_text_question(
            message="Router ID",
            instruction="Используйте lowercase, цифры, дефисы и подчёркивания.",
            style=_style(),
            input=input,
            output=output,
        ),
        user_input="router-main\r",
        message="Router ID",
        render_answer=lambda value: value,
    )
    assert result == "router-main"
    assert history == "> Router ID router-main\n"
    assert "Используйте lowercase, цифры, дефисы и подчёркивания." not in history
    assert DEFAULT_TEXT_FOOTER not in history


def test_confirm_question_keeps_footer_only_while_active() -> None:
    question = _build_confirm_question(
        message="Сохранить?",
        style=_style(),
    )
    assert _footer_text(question) == DEFAULT_CONFIRM_FOOTER

    result, history = _run_history_entry(
        lambda *, input, output: _build_confirm_question(
            message="Сохранить?",
            style=_style(),
            input=input,
            output=output,
        ),
        user_input="y\r",
        message="Сохранить?",
        render_answer=lambda value: "да" if value is True else None,
    )
    assert result is True
    assert history == "> Сохранить? да\n"
    assert DEFAULT_CONFIRM_FOOTER not in history


def _run_question(
    builder: Any,
    *,
    user_input: str,
) -> tuple[Any, str, str]:
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

    raw = stream.getvalue()
    return result, _normalize_rendered_text(raw), raw


def _normalize_rendered_text(rendered: str) -> str:
    text = ANSI_PATTERN.sub("", rendered)
    return text.replace("\r", "")


def _run_history_entry(
    builder: Any,
    *,
    user_input: str,
    message: str,
    render_answer: Any,
) -> tuple[Any, str]:
    prompt_stream = StringIO()
    output = Vt100_Output(
        prompt_stream,
        lambda: Size(rows=12, columns=100),
        term="xterm",
        enable_bell=False,
        enable_cpr=False,
    )
    history_stream = StringIO()
    console = Console(file=history_stream, force_terminal=False, color_system=None)

    with create_pipe_input() as input:
        question = builder(input=input, output=output)
        input.send_text(user_input)
        result = _ask_and_echo(
            question=question,
            console=console,
            message=message,
            render_answer=render_answer,
        )

    return result, history_stream.getvalue()


def _footer_text(question: Any) -> str:
    footer_container = question.application.layout.container.children[-1]
    footer_window = getattr(footer_container, "content", footer_container)
    footer_control = footer_window.content
    footer_tokens = footer_control.text() if callable(footer_control.text) else footer_control.text
    return "".join(token[1] for token in footer_tokens).strip()


def _style():
    return QuestionaryPromptAdapter()._style
