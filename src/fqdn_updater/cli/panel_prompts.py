from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import questionary
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from questionary.question import Question
from rich.console import Console

DEFAULT_SELECT_FOOTER = "Стрелки выбирают, Enter открывает, Esc назад."
DEFAULT_CHECKBOX_FOOTER = "Стрелки выбирают, Пробел отмечает, Enter сохраняет, Esc назад."
DEFAULT_TEXT_FOOTER = "Enter подтверждает, Esc назад."
DEFAULT_CONFIRM_FOOTER = "Y/N выбирают, Enter подтверждает, Esc назад."
DEFAULT_PAUSE_FOOTER = "Любая клавиша продолжает."
HIDDEN_INSTRUCTION = "\u200b"


@dataclass(frozen=True)
class PromptChoice:
    title: str
    value: str
    disabled: str | None = None
    checked: bool = False


class PromptAdapter(Protocol):
    def select(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        default: str | None = None,
        instruction: str | None = None,
    ) -> str | None: ...

    def checkbox(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        instruction: str | None = None,
    ) -> list[str] | None: ...

    def text(
        self,
        *,
        message: str,
        default: str = "",
        instruction: str | None = None,
    ) -> str | None: ...

    def confirm(
        self,
        *,
        message: str,
        default: bool = True,
        instruction: str | None = None,
    ) -> bool | None: ...

    def pause(self, *, message: str) -> None: ...


class QuestionaryPromptAdapter:
    def __init__(self, *, console: Console | None = None) -> None:
        self._console = console
        self._style = Style.from_dict(
            {
                "question": "bold",
                "qmark": "fg:#00d7ff bold",
                "answer": "fg:#5fd700 bold",
                "pointer": "fg:#ffaf00 bold",
                "highlighted": "fg:#00d7ff bold",
                "selected": "fg:#5fd700",
                "instruction": "fg:#808080 italic",
                "footer": "fg:#808080",
                "disabled": "fg:#808080 italic",
            }
        )

    def select(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        default: str | None = None,
        instruction: str | None = None,
    ) -> str | None:
        return _build_select_question(
            message=message,
            choices=choices,
            default=default,
            instruction=instruction,
            style=self._style,
        ).ask()

    def checkbox(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        instruction: str | None = None,
    ) -> list[str] | None:
        return _build_checkbox_question(
            message=message,
            choices=choices,
            instruction=instruction,
            style=self._style,
        ).ask()

    def text(
        self,
        *,
        message: str,
        default: str = "",
        instruction: str | None = None,
    ) -> str | None:
        return _build_text_question(
            message=message,
            default=default,
            instruction=instruction,
            style=self._style,
        ).ask()

    def confirm(
        self,
        *,
        message: str,
        default: bool = True,
        instruction: str | None = None,
    ) -> bool | None:
        return _build_confirm_question(
            message=message,
            default=default,
            instruction=instruction,
            style=self._style,
        ).ask()

    def pause(self, *, message: str) -> None:
        _build_pause_question(
            message=message,
            style=self._style,
        ).ask()

    def _build_choice(self, choice: PromptChoice) -> questionary.Choice:
        return questionary.Choice(
            title=choice.title,
            value=choice.value,
            disabled=choice.disabled,
            checked=choice.checked,
        )


def _build_select_question(
    *,
    message: str,
    choices: list[PromptChoice],
    default: str | None = None,
    instruction: str | None = None,
    style: Style,
    **kwargs: Any,
) -> Question:
    question = questionary.select(
        message=message,
        choices=[_build_choice(choice) for choice in choices],
        default=default,
        instruction=HIDDEN_INSTRUCTION,
        qmark=">",
        pointer=">",
        style=style,
        use_arrow_keys=True,
        use_jk_keys=True,
        use_emacs_keys=True,
        **kwargs,
    )
    return _decorate_question(
        question=question,
        footer=instruction or DEFAULT_SELECT_FOOTER,
    )


def _build_checkbox_question(
    *,
    message: str,
    choices: list[PromptChoice],
    instruction: str | None = None,
    style: Style,
    **kwargs: Any,
) -> Question:
    question = questionary.checkbox(
        message=message,
        choices=[_build_choice(choice) for choice in choices],
        instruction=HIDDEN_INSTRUCTION,
        qmark=">",
        pointer=">",
        style=style,
        use_arrow_keys=True,
        use_jk_keys=True,
        use_emacs_keys=True,
        **kwargs,
    )
    return _decorate_question(
        question=question,
        footer=instruction or DEFAULT_CHECKBOX_FOOTER,
    )


def _build_text_question(
    *,
    message: str,
    default: str = "",
    instruction: str | None = None,
    style: Style,
    **kwargs: Any,
) -> Question:
    question = questionary.text(
        message=message,
        default=default,
        instruction=instruction,
        qmark=">",
        style=style,
        **kwargs,
    )
    return _decorate_question(
        question=question,
        footer=DEFAULT_TEXT_FOOTER,
    )


def _build_confirm_question(
    *,
    message: str,
    default: bool = True,
    instruction: str | None = None,
    style: Style,
    **kwargs: Any,
) -> Question:
    question = questionary.confirm(
        message=message,
        default=default,
        instruction=instruction if instruction is not None else HIDDEN_INSTRUCTION,
        auto_enter=False,
        qmark=">",
        style=style,
        **kwargs,
    )
    return _decorate_question(
        question=question,
        footer=DEFAULT_CONFIRM_FOOTER,
    )


def _build_pause_question(
    *,
    message: str,
    style: Style,
    **kwargs: Any,
) -> Question:
    question = questionary.press_any_key_to_continue(
        message=message,
        style=style,
        **kwargs,
    )
    return _decorate_question(
        question=question,
        footer=DEFAULT_PAUSE_FOOTER,
        escape_returns_none=False,
    )


def _decorate_question(
    *,
    question: Question,
    footer: str,
    escape_returns_none: bool = True,
) -> Question:
    if escape_returns_none:
        bindings = KeyBindings()

        @bindings.add(Keys.Escape)
        def _cancel(event) -> None:
            event.app.exit(result=None)

        question.application.key_bindings = merge_key_bindings(
            [question.application.key_bindings, bindings]
        )

    footer_window = Window(
        height=1,
        content=FormattedTextControl(lambda: [("class:footer", f" {footer}")]),
    )
    question.application.layout = Layout(
        HSplit(
            [
                question.application.layout.container,
                footer_window,
            ]
        )
    )
    return question


def _build_choice(choice: PromptChoice) -> questionary.Choice:
    return questionary.Choice(
        title=choice.title,
        value=choice.value,
        disabled=choice.disabled,
        checked=choice.checked,
    )
