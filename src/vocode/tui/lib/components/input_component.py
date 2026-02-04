from __future__ import annotations

import typing
from dataclasses import dataclass

from rich import console as rich_console
from rich import style as rich_style
from rich import text as rich_text

from vocode.tui.lib import base as tui_base
from vocode.tui.lib.input import base as input_base

from . import text_editor as components_text_editor


CURSOR_STYLE: typing.Final[rich_style.Style] = rich_style.Style(reverse=True)


@dataclass(frozen=True)
class KeyBinding:
    key: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False


class InputComponent(tui_base.Component):
    def __init__(
        self,
        text: str = "",
        id: str | None = None,
        single_line: bool = False,
        component_style: tui_base.ComponentStyle | None = None,
        prefix: str | None = None,
        submit_with_enter: bool = False,
    ) -> None:
        super().__init__(
            id=id,
            component_style=component_style,
        )
        self._editor = components_text_editor.TextEditor(text)
        self._editor.subscribe_cursor_event(self._handle_editor_cursor_event)
        self._single_line = single_line
        self._submit_with_enter = submit_with_enter
        self._keymap = self._create_keymap()
        self._submit_subscribers: list[typing.Callable[[str], None]] = []
        self._change_subscribers: list[typing.Callable[[str], None]] = []
        self._cursor_event_subscribers: list[typing.Callable[[int, int], None]] = []
        self._prefix = prefix
        self._top_line: int = 0
        self._view_height: int | None = None

    @property
    def text(self) -> str:
        return self._editor.text

    @text.setter
    def text(self, value: str) -> None:
        self._editor.text = value
        self._mark_dirty()

    @property
    def prefix(self) -> str | None:
        return self._prefix

    @prefix.setter
    def prefix(self, value: str | None) -> None:
        if self._prefix == value:
            return
        self._prefix = value
        self._mark_dirty()

    @property
    def lines(self) -> typing.List[str]:
        return self._editor.lines

    @property
    def scroll_top(self) -> int:
        return self._top_line

    @property
    def total_lines(self) -> int:
        return len(self._editor.lines)

    @property
    def cursor_row(self) -> int:
        return self._editor.cursor_row

    @property
    def cursor_col(self) -> int:
        return self._editor.cursor_col

    def set_cursor_position(self, row: int, col: int) -> None:
        self._editor.set_cursor_position(row, col)
        self._mark_dirty()

    def _handle_editor_cursor_event(self, row: int, col: int) -> None:
        self._update_view_height()
        self._ensure_cursor_visible()
        for subscriber in list(self._cursor_event_subscribers):
            subscriber(row, col)

    def _update_view_height(self) -> None:
        terminal = self.terminal
        if terminal is None:
            self._view_height = None
            self._top_line = 0
            return
        height = terminal.console.size.height
        if height <= 0:
            self._view_height = None
            self._top_line = 0
            return
        max_height = int(height * 2 / 3)
        if max_height < 1:
            max_height = 1
        total = len(self._editor.lines)
        if total <= 0:
            self._view_height = 0
            self._top_line = 0
            return
        if max_height > total:
            max_height = total
        self._view_height = max_height
        max_top = max(total - max_height, 0)
        if self._top_line > max_top:
            self._top_line = max_top
        if self._top_line < 0:
            self._top_line = 0

    def _ensure_cursor_visible(self) -> None:
        height = self._view_height
        if height is None or height <= 0:
            return
        total = len(self._editor.lines)
        if total <= 0:
            self._top_line = 0
            return
        max_top = max(total - height, 0)
        if self._top_line < 0:
            self._top_line = 0
        if self._top_line > max_top:
            self._top_line = max_top
        row = self._editor.cursor_row
        if row < self._top_line:
            self._top_line = row
        elif row >= self._top_line + height:
            self._top_line = row - (height - 1)
        if self._top_line < 0:
            self._top_line = 0
        if self._top_line > max_top:
            self._top_line = max_top

    def _create_keymap(self) -> dict[KeyBinding, typing.Callable[[], None]]:
        keymap: dict[KeyBinding, typing.Callable[[], None]] = {
            KeyBinding("left"): self.move_cursor_left,
            KeyBinding("right"): self.move_cursor_right,
            KeyBinding("up"): self.move_cursor_up,
            KeyBinding("down"): self.move_cursor_down,
            KeyBinding("left", alt=True): self.move_cursor_word_left,
            KeyBinding("right", alt=True): self.move_cursor_word_right,
            KeyBinding("home"): self.move_cursor_line_start,
            KeyBinding("end"): self.move_cursor_line_end,
            KeyBinding("a", ctrl=True): self.move_cursor_line_start,
            KeyBinding("e", ctrl=True): self.move_cursor_line_end,
            KeyBinding("b", ctrl=True): self.move_cursor_left,
            KeyBinding("f", ctrl=True): self.move_cursor_right,
            KeyBinding("p", ctrl=True): self.move_cursor_up,
            KeyBinding("n", ctrl=True): self.move_cursor_down,
            KeyBinding("b", alt=True): self.move_cursor_word_left,
            KeyBinding("f", alt=True): self.move_cursor_word_right,
            KeyBinding("backspace"): self.backspace,
            KeyBinding("delete"): self.delete,
            KeyBinding("d", ctrl=True): self.delete,
            KeyBinding("k", ctrl=True): self.kill_to_line_end,
            KeyBinding("u", ctrl=True): self.kill_to_line_start,
            KeyBinding("w", ctrl=True): self.kill_word_backward,
            KeyBinding("d", alt=True): self.kill_word_forward,
            KeyBinding("u", alt=True): self.uppercase_word,
            KeyBinding("l", alt=True): self.lowercase_word,
            KeyBinding("c", alt=True): self.capitalize_word,
        }
        if self._single_line:
            keymap[KeyBinding("enter")] = self.submit
        else:
            if self._submit_with_enter:
                keymap[KeyBinding("enter")] = self.submit
                keymap[KeyBinding("enter", alt=True)] = self.break_line
            else:
                keymap[KeyBinding("enter")] = self.break_line
                keymap[KeyBinding("enter", alt=True)] = self.submit
        return keymap

    def subscribe_submit(self, subscriber: typing.Callable[[str], None]) -> None:
        self._submit_subscribers.append(subscriber)

    def subscribe_change(self, subscriber: typing.Callable[[str], None]) -> None:
        self._change_subscribers.append(subscriber)

    def subscribe_cursor_event(
        self, subscriber: typing.Callable[[int, int], None]
    ) -> None:
        self._cursor_event_subscribers.append(subscriber)

    def submit(self) -> None:
        value = self.text
        for subscriber in list(self._submit_subscribers):
            subscriber(value)

    def _notify_change(self) -> None:
        if not self._change_subscribers:
            return
        value = self.text
        for subscriber in list(self._change_subscribers):
            subscriber(value)

    def _mark_dirty(self) -> None:
        super()._mark_dirty()
        self._notify_change()

    def render(self, options: rich_console.ConsoleOptions) -> tui_base.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        console = terminal.console
        self._update_view_height()
        self._ensure_cursor_visible()
        text = self._build_text_with_cursor()
        styled = self.apply_style(text)
        rendered = console.render_lines(
            styled,
            options=options,
            pad=False,
            new_lines=False,
        )
        return typing.cast(tui_base.Lines, rendered)

    def _build_text_with_cursor(self) -> rich_text.Text:
        full = rich_text.Text()
        prefix = self._prefix
        prefix_len = len(prefix) if prefix is not None else 0
        cursor_row = self._editor.cursor_row
        cursor_col = self._editor.cursor_col
        lines = self._editor.lines
        total = len(lines)
        if self._view_height is None or self._view_height <= 0:
            start_row = 0
            end_row = total
        else:
            start_row = self._top_line
            end_row = self._top_line + self._view_height
            if end_row > total:
                end_row = total
        for index in range(start_row, end_row):
            raw = lines[index]
            if index > start_row:
                full.append("\n")
            if prefix is not None:
                if index == 0:
                    prefix_part = prefix
                else:
                    prefix_part = " " * prefix_len
            else:
                prefix_part = ""

            display_line = raw
            cursor_index_in_raw = None
            if index == cursor_row:
                if cursor_col >= len(raw):
                    display_line = raw + " "
                    cursor_index_in_raw = len(raw)
                else:
                    cursor_index_in_raw = cursor_col
            line_text = rich_text.Text(
                prefix_part + display_line,
                overflow="fold",
                no_wrap=False,
            )
            if index == cursor_row and cursor_index_in_raw is not None:
                start = prefix_len + cursor_index_in_raw
                line_text.stylize(
                    CURSOR_STYLE,
                    start,
                    start + 1,
                )
            full.append_text(line_text)
        return full

    def move_cursor_left(self) -> None:
        self._editor.move_cursor_left()
        self._mark_dirty()

    def move_cursor_right(self) -> None:
        self._editor.move_cursor_right()
        self._mark_dirty()

    def move_cursor_up(self) -> None:
        self._editor.move_cursor_up()
        self._mark_dirty()

    def move_cursor_down(self) -> None:
        self._editor.move_cursor_down()
        self._mark_dirty()

    def move_cursor_line_start(self) -> None:
        self._editor.move_cursor_line_start()
        self._mark_dirty()

    def move_cursor_line_end(self) -> None:
        self._editor.move_cursor_line_end()
        self._mark_dirty()

    def move_cursor_word_left(self) -> None:
        self._editor.move_cursor_word_left()
        self._mark_dirty()

    def move_cursor_word_right(self) -> None:
        self._editor.move_cursor_word_right()
        self._mark_dirty()

    def insert_char(self, ch: str) -> None:
        self._editor.insert_char(ch)
        self._mark_dirty()

    def backspace(self) -> None:
        self._editor.backspace()
        self._mark_dirty()

    def delete(self) -> None:
        self._editor.delete()
        self._mark_dirty()

    def kill_to_line_end(self) -> None:
        self._editor.kill_to_line_end()
        self._mark_dirty()

    def kill_to_line_start(self) -> None:
        self._editor.kill_to_line_start()
        self._mark_dirty()

    def kill_word_backward(self) -> None:
        self._editor.kill_word_backward()
        self._mark_dirty()

    def kill_word_forward(self) -> None:
        self._editor.kill_word_forward()
        self._mark_dirty()

    def uppercase_word(self) -> None:
        self._editor.uppercase_word()
        self._mark_dirty()

    def lowercase_word(self) -> None:
        self._editor.lowercase_word()
        self._mark_dirty()

    def capitalize_word(self) -> None:
        self._editor.capitalize_word()
        self._mark_dirty()

    def break_line(self) -> None:
        self._editor.break_line()
        self._mark_dirty()

    def paste_text(self, text: str) -> None:
        if not text:
            return
        self._editor.insert_text(text)
        self._mark_dirty()

    def on_key_event(self, event: input_base.KeyEvent) -> None:
        if event.action != "down":
            return
        binding = KeyBinding(
            key=event.key,
            ctrl=event.ctrl,
            alt=event.alt,
            shift=event.shift,
        )
        handler = self._keymap.get(binding)
        if handler is not None:
            handler()
            return
        if event.text:
            for ch in event.text:
                self.insert_char(ch)

    def on_mouse_event(self, event: input_base.MouseEvent) -> None:
        return
