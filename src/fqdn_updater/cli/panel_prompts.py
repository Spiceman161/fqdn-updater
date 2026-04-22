from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import questionary
from prompt_toolkit.styles import Style
from rich.console import Console


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
        return questionary.select(
            message=message,
            choices=[self._build_choice(choice) for choice in choices],
            default=default,
            instruction=instruction,
            qmark=">",
            pointer=">",
            style=self._style,
            use_arrow_keys=True,
            use_jk_keys=True,
            use_emacs_keys=True,
        ).ask()

    def checkbox(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        instruction: str | None = None,
    ) -> list[str] | None:
        return questionary.checkbox(
            message=message,
            choices=[self._build_choice(choice) for choice in choices],
            instruction=instruction,
            qmark=">",
            pointer=">",
            style=self._style,
            use_arrow_keys=True,
            use_jk_keys=True,
            use_emacs_keys=True,
        ).ask()

    def text(
        self,
        *,
        message: str,
        default: str = "",
        instruction: str | None = None,
    ) -> str | None:
        return questionary.text(
            message=message,
            default=default,
            instruction=instruction,
            qmark=">",
            style=self._style,
        ).ask()

    def confirm(
        self,
        *,
        message: str,
        default: bool = True,
        instruction: str | None = None,
    ) -> bool | None:
        return questionary.confirm(
            message=message,
            default=default,
            instruction=instruction,
            auto_enter=False,
            qmark=">",
            style=self._style,
        ).ask()

    def pause(self, *, message: str) -> None:
        questionary.press_any_key_to_continue(
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
