from __future__ import annotations

import textwrap
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import questionary
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.filters import IsDone
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from questionary.prompts.common import InquirerControl
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
    answer_title: str | None = None


@dataclass(frozen=True)
class CheckboxTableMeta:
    header: str
    summary: Callable[[tuple[str, ...]], str | list[tuple[str, str]]]
    selection_groups: dict[str, tuple[str, ...]] | None = None


class PromptAdapter(Protocol):
    def select(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        default: str | None = None,
        instruction: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> str | None: ...

    def checkbox(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        instruction: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
        table_meta: CheckboxTableMeta | None = None,
    ) -> list[str] | None: ...

    def text(
        self,
        *,
        message: str,
        default: str = "",
        instruction: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> str | None: ...

    def confirm(
        self,
        *,
        message: str,
        default: bool = True,
        instruction: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> bool | None: ...

    def history_select(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        default: str | None = None,
        page_label: str,
        has_previous_page: bool,
        has_next_page: bool,
    ) -> str | None: ...

    def pause(self, *, message: str, hint_lines: tuple[str, ...] | None = None) -> None: ...


class QuestionaryPromptAdapter:
    def __init__(self, *, console: Console | None = None) -> None:
        self._console = console
        self._style = Style.from_dict(
            {
                "question": "bold",
                "qmark": "fg:#00d7ff bold",
                "answer": "fg:#5fd700 bold",
                "pointer": "fg:#ffaf00 bold",
                "highlighted": "noreverse",
                "selected": "noreverse",
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
        hint_lines: tuple[str, ...] | None = None,
    ) -> str | None:
        choice_titles = {choice.value: (choice.answer_title or choice.title) for choice in choices}
        return _ask_and_echo(
            question=_build_select_question(
                message=message,
                choices=choices,
                default=default,
                instruction=instruction,
                hint_lines=hint_lines,
                style=self._style,
            ),
            console=self._console,
            message=message,
            render_answer=lambda result: choice_titles.get(result),
        )

    def checkbox(
        self,
        *,
        message: str,
        choices: list[PromptChoice],
        instruction: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
        table_meta: CheckboxTableMeta | None = None,
    ) -> list[str] | None:
        choice_titles = {
            choice.value: (
                choice.answer_title or (choice.value if table_meta is not None else choice.title)
            )
            for choice in choices
        }
        return _ask_and_echo(
            question=_build_checkbox_question(
                message=message,
                choices=choices,
                instruction=instruction,
                hint_lines=hint_lines,
                table_meta=table_meta,
                style=self._style,
            ),
            console=self._console,
            message=message,
            render_answer=lambda result: _render_checkbox_answer(
                result=result,
                choice_titles=choice_titles,
            ),
        )

    def text(
        self,
        *,
        message: str,
        default: str = "",
        instruction: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> str | None:
        return _ask_and_echo(
            question=_build_text_question(
                message=message,
                default=default,
                instruction=instruction,
                hint_lines=hint_lines,
                style=self._style,
            ),
            console=self._console,
            message=message,
            render_answer=lambda result: result,
        )

    def confirm(
        self,
        *,
        message: str,
        default: bool = True,
        instruction: str | None = None,
        hint_lines: tuple[str, ...] | None = None,
    ) -> bool | None:
        return _ask_and_echo(
            question=_build_confirm_question(
                message=message,
                default=default,
                instruction=instruction,
                hint_lines=hint_lines,
                style=self._style,
            ),
            console=self._console,
            message=message,
            render_answer=lambda result: (
                "да" if result is True else "нет" if result is False else None
            ),
        )

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
        choice_titles = {choice.value: (choice.answer_title or choice.title) for choice in choices}
        return _ask_and_echo(
            question=_build_select_question(
                message=f"{message}\n{page_label}",
                choices=choices,
                default=default,
                instruction="↑/↓ запись, Enter открыть, ←/→ страницы, Esc главное меню.",
                hint_lines=None,
                style=self._style,
                left_result="prev-page" if has_previous_page else None,
                right_result="next-page" if has_next_page else None,
            ),
            console=self._console,
            message=message,
            render_answer=lambda result: choice_titles.get(result),
        )

    def pause(self, *, message: str, hint_lines: tuple[str, ...] | None = None) -> None:
        _ask_and_echo(
            question=_build_pause_question(
                message=message,
                hint_lines=hint_lines,
                style=self._style,
            ),
            console=self._console,
            message=message,
            render_answer=lambda _result: None,
        )

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
    hint_lines: tuple[str, ...] | None = None,
    style: Style,
    left_result: str | None = None,
    right_result: str | None = None,
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
        hint_lines=hint_lines,
        footer=instruction or DEFAULT_SELECT_FOOTER,
        left_result=left_result,
        right_result=right_result,
    )


def _build_checkbox_question(
    *,
    message: str,
    choices: list[PromptChoice],
    instruction: str | None = None,
    hint_lines: tuple[str, ...] | None = None,
    table_meta: CheckboxTableMeta | None = None,
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
    if table_meta is not None:
        _install_checkbox_table_meta(question=question, table_meta=table_meta)
        if table_meta.selection_groups:
            _install_checkbox_selection_groups(
                question=question,
                selection_groups=table_meta.selection_groups,
            )
    return _decorate_question(
        question=question,
        hint_lines=hint_lines,
        footer=instruction or DEFAULT_CHECKBOX_FOOTER,
    )


def _build_text_question(
    *,
    message: str,
    default: str = "",
    instruction: str | None = None,
    hint_lines: tuple[str, ...] | None = None,
    style: Style,
    **kwargs: Any,
) -> Question:
    question = questionary.text(
        message=message,
        default=default,
        instruction=HIDDEN_INSTRUCTION,
        qmark=">",
        style=style,
        **kwargs,
    )
    return _decorate_question(
        question=question,
        hint_lines=hint_lines,
        footer=_compose_footer(instruction=instruction, default_footer=DEFAULT_TEXT_FOOTER),
    )


def _build_confirm_question(
    *,
    message: str,
    default: bool = True,
    instruction: str | None = None,
    hint_lines: tuple[str, ...] | None = None,
    style: Style,
    **kwargs: Any,
) -> Question:
    question = questionary.confirm(
        message=message,
        default=default,
        instruction=HIDDEN_INSTRUCTION,
        auto_enter=False,
        qmark=">",
        style=style,
        **kwargs,
    )
    return _decorate_question(
        question=question,
        hint_lines=hint_lines,
        footer=_compose_footer(instruction=instruction, default_footer=DEFAULT_CONFIRM_FOOTER),
    )


def _build_pause_question(
    *,
    message: str,
    hint_lines: tuple[str, ...] | None = None,
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
        hint_lines=hint_lines,
        footer=DEFAULT_PAUSE_FOOTER,
        escape_returns_none=False,
    )


def _decorate_question(
    *,
    question: Question,
    hint_lines: tuple[str, ...] | None,
    footer: str,
    escape_returns_none: bool = True,
    left_result: str | None = None,
    right_result: str | None = None,
) -> Question:
    if escape_returns_none:
        bindings = KeyBindings()

        @bindings.add(Keys.Escape)
        def _cancel(event) -> None:
            event.app.exit(result=None)

        @bindings.add(Keys.Left, eager=True)
        def _previous_page(event) -> None:
            if left_result is not None:
                event.app.exit(result=left_result)
                return
            if _question_has_choice_value(question, "prev-page"):
                event.app.exit(result="prev-page")

        @bindings.add(Keys.Right, eager=True)
        def _next_page(event) -> None:
            if right_result is not None:
                event.app.exit(result=right_result)
                return
            if _question_has_choice_value(question, "next-page"):
                event.app.exit(result="next-page")

        question.application.key_bindings = merge_key_bindings(
            [question.application.key_bindings, bindings]
        )

    hint_window = ConditionalContainer(
        Window(
            content=FormattedTextControl(
                lambda: [("class:footer", _render_hint_panel(hint_lines))]
            ),
            dont_extend_height=True,
        ),
        filter=~IsDone() if hint_lines else False,
    )
    footer_window = ConditionalContainer(
        Window(
            height=1,
            content=FormattedTextControl(lambda: [("class:footer", f" {footer}")]),
        ),
        filter=~IsDone(),
    )
    question.application.layout = Layout(
        HSplit(
            [
                hint_window,
                question.application.layout.container,
                footer_window,
            ]
        )
    )
    return question


def _install_checkbox_table_meta(
    *,
    question: Question,
    table_meta: CheckboxTableMeta,
) -> None:
    container = question.application.layout.container
    if not isinstance(container, HSplit):
        return

    inquirer_index: int | None = None
    inquirer_control: InquirerControl | None = None
    for index, child in enumerate(container.children):
        if not isinstance(child, ConditionalContainer):
            continue
        if not isinstance(child.content, Window):
            continue
        if not isinstance(child.content.content, InquirerControl):
            continue
        inquirer_index = index
        inquirer_control = child.content.content
        break

    if inquirer_index is None or inquirer_control is None:
        return

    header_window = ConditionalContainer(
        Window(
            height=1,
            dont_extend_height=True,
            content=FormattedTextControl(lambda: [("class:footer", f"     {table_meta.header}")]),
        ),
        filter=~IsDone(),
    )
    summary_window = ConditionalContainer(
        Window(
            height=1,
            dont_extend_height=True,
            content=FormattedTextControl(
                lambda: _checkbox_summary_tokens(
                    table_meta.summary(tuple(inquirer_control.selected_options))
                )
            ),
        ),
        filter=~IsDone(),
    )

    container.children.insert(inquirer_index, header_window)
    container.children.insert(inquirer_index + 2, summary_window)


def _install_checkbox_selection_groups(
    *,
    question: Question,
    selection_groups: dict[str, tuple[str, ...]],
) -> None:
    inquirer_control = _find_inquirer_control(question)
    if inquirer_control is None:
        return

    choice_order = [choice.value for choice in inquirer_control.choices]
    bindings = KeyBindings()

    @bindings.add(" ", eager=True)
    def _toggle_grouped_selection(_event) -> None:
        pointed_choice = inquirer_control.get_pointed_at().value
        inquirer_control.selected_options = _toggle_checkbox_group_value(
            selected_values=inquirer_control.selected_options,
            pointed_value=pointed_choice,
            selection_groups=selection_groups,
            choice_order=choice_order,
        )

    @bindings.add("a", eager=True)
    def _toggle_all_grouped_selection(_event) -> None:
        enabled_values = [
            choice.value for choice in inquirer_control.choices if not choice.disabled
        ]
        if set(enabled_values).issubset(inquirer_control.selected_options):
            inquirer_control.selected_options = []
            return
        inquirer_control.selected_options = enabled_values

    @bindings.add("i", eager=True)
    def _invert_grouped_selection(_event) -> None:
        selected = set(inquirer_control.selected_options)
        inverted = [
            choice.value
            for choice in inquirer_control.choices
            if not choice.disabled and choice.value not in selected
        ]
        inquirer_control.selected_options = _complete_checkbox_group_selection(
            selected_values=inverted,
            selection_groups=selection_groups,
            choice_order=choice_order,
        )

    question.application.key_bindings = merge_key_bindings(
        [question.application.key_bindings, bindings]
    )


def _find_inquirer_control(question: Question) -> InquirerControl | None:
    container = question.application.layout.container
    if not isinstance(container, HSplit):
        return None

    for child in container.children:
        if not isinstance(child, ConditionalContainer):
            continue
        if not isinstance(child.content, Window):
            continue
        if isinstance(child.content.content, InquirerControl):
            return child.content.content
    return None


def _question_has_choice_value(question: Question, value: str) -> bool:
    inquirer_control = _find_inquirer_control(question)
    if inquirer_control is None:
        return False
    return any(choice.value == value for choice in inquirer_control.choices)


def _toggle_checkbox_group_value(
    *,
    selected_values: Sequence[Any],
    pointed_value: Any,
    selection_groups: dict[str, tuple[str, ...]],
    choice_order: Sequence[Any],
) -> list[Any]:
    selected = set(selected_values)
    parent_by_child = {
        child: parent for parent, children in selection_groups.items() for child in children
    }

    if pointed_value in selection_groups:
        children = selection_groups[pointed_value]
        if pointed_value in selected:
            selected.discard(pointed_value)
            selected.difference_update(children)
        else:
            selected.add(pointed_value)
            selected.update(children)
    elif pointed_value in parent_by_child:
        parent = parent_by_child[pointed_value]
        children = selection_groups[parent]
        if pointed_value in selected:
            selected.discard(pointed_value)
            selected.discard(parent)
        else:
            selected.add(pointed_value)
            if all(child in selected for child in children):
                selected.add(parent)
    elif pointed_value in selected:
        selected.discard(pointed_value)
    else:
        selected.add(pointed_value)

    return [value for value in choice_order if value in selected]


def _complete_checkbox_group_selection(
    *,
    selected_values: Sequence[Any],
    selection_groups: dict[str, tuple[str, ...]],
    choice_order: Sequence[Any],
) -> list[Any]:
    selected = set(selected_values)
    for parent, children in selection_groups.items():
        if parent in selected:
            selected.update(children)
        elif all(child in selected for child in children):
            selected.add(parent)
    return [value for value in choice_order if value in selected]


def _checkbox_summary_tokens(
    summary: str | list[tuple[str, str]],
) -> list[tuple[str, str]]:
    if isinstance(summary, str):
        return [("class:footer", f"     {summary}")]
    return [("class:footer", "     "), *summary]


def _compose_footer(*, instruction: str | None, default_footer: str) -> str:
    if not instruction:
        return default_footer
    return f"{instruction} {default_footer}"


def _render_checkbox_answer(
    *,
    result: list[str] | None,
    choice_titles: dict[str, str],
) -> str | None:
    if result is None:
        return None
    return ", ".join(choice_titles[value] for value in result if value in choice_titles)


def _render_hint_panel(hint_lines: tuple[str, ...] | None) -> str:
    if not hint_lines:
        return ""

    terminal_width = 100
    app = get_app_or_none()
    if app is not None:
        terminal_width = max(app.output.get_size().columns, 20)

    inner_width = max(terminal_width - 2, 18)
    content_width = max(inner_width - 2, 10)
    wrapped_lines: list[str] = []
    for line in hint_lines:
        wrapped_lines.extend(textwrap.wrap(line, width=content_width) or [""])

    title = " Подсказка "
    title_fill = max(inner_width - len(title), 0)
    title_left = title_fill // 2
    title_right = title_fill - title_left

    rendered_lines = [
        "╭" + ("─" * title_left) + title + ("─" * title_right) + "╮",
        *[f"│ {line.ljust(content_width)} │" for line in wrapped_lines],
        "╰" + ("─" * inner_width) + "╯",
    ]
    return "\n".join(rendered_lines)


def _ask_and_echo(
    *,
    question: Question,
    console: Console | None,
    message: str,
    render_answer: Callable[[Any], str | None],
) -> Any:
    question.application.erase_when_done = True
    result = question.ask()
    answer = render_answer(result)
    if answer is not None:
        _print_history_line(console=console, message=message, answer=answer)
    return result


def _print_history_line(*, console: Console | None, message: str, answer: str) -> None:
    line = f"> {message}" if answer == "" else f"> {message} {answer}"
    if console is not None:
        console.print(line, markup=False, highlight=False)
        return
    print(line)


def _build_choice(choice: PromptChoice) -> questionary.Choice:
    return questionary.Choice(
        title=choice.title,
        value=choice.value,
        disabled=choice.disabled,
        checked=choice.checked,
    )
