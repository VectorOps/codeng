from __future__ import annotations

import typing

from dataclasses import dataclass
from rich import console as rich_console
from rich import segment as rich_segment
from rich import style as rich_style
from rich import text as rich_text

from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.lib.input import base as input_base


Lines = tui_terminal.Lines
CURSOR_STYLE: typing.Final[rich_style.Style] = rich_style.Style(reverse=True)


@dataclass(frozen=True)
class KeyBinding:
    key: str
    ctrl: bool = False
    alt: bool = False
    shift: bool = False


class InputComponent(tui_terminal.Component):
    def __init__(self, text: str = "", id: str | None = None) -> None:
        super().__init__(id=id)
        lines = text.splitlines() if text else []
        if not lines:
            lines = [""]
        self._lines: list[str] = lines
        last_row = len(self._lines) - 1
        last_line = self._lines[last_row]
        if last_line:
            self._cursor_row = last_row
            self._cursor_col = len(last_line) - 1
        else:
            self._cursor_row = last_row
            self._cursor_col = 0
        self._keymap = self._create_keymap()
        self._submit_subscribers: list[typing.Callable[[str], None]] = []

    @property
    def text(self) -> str:
        return "\n".join(self._lines)

    @text.setter
    def text(self, value: str) -> None:
        lines = value.splitlines() if value else []
        if not lines:
            lines = [""]
        self._lines = lines
        last_row = len(self._lines) - 1
        last_line = self._lines[last_row]
        if last_line:
            self._cursor_row = last_row
            self._cursor_col = len(last_line) - 1
        else:
            self._cursor_row = last_row
            self._cursor_col = 0
        self._mark_dirty()

    @property
    def cursor_row(self) -> int:
        return self._cursor_row

    @property
    def cursor_col(self) -> int:
        return self._cursor_col

    def _mark_dirty(self) -> None:
        terminal = self.terminal
        if terminal is not None:
            terminal.notify_component(self)

    def _create_keymap(self) -> dict[KeyBinding, typing.Callable[[], None]]:
        return {
            KeyBinding("left"): self.move_cursor_left,
            KeyBinding("right"): self.move_cursor_right,
            KeyBinding("up"): self.move_cursor_up,
            KeyBinding("down"): self.move_cursor_down,
            KeyBinding("backspace"): self.backspace,
            KeyBinding("delete"): self.delete,
            KeyBinding("enter"): self.break_line,
            KeyBinding("enter", alt=True): self.submit,
        }

    def subscribe_submit(self, subscriber: typing.Callable[[str], None]) -> None:
        self._submit_subscribers.append(subscriber)

    def submit(self) -> None:
        value = self.text
        for subscriber in list(self._submit_subscribers):
            subscriber(value)

    def render(self) -> Lines:
        terminal = self.terminal
        if terminal is None:
            console = rich_console.Console()
        else:
            console = terminal.console
        return self._render_lines_with_cursor(self._lines, console)

    def _render_lines_with_cursor(
        self,
        lines: typing.Iterable[str],
        console: rich_console.Console,
    ) -> Lines:
        rendered: Lines = []
        for row, raw in enumerate(lines):
            text = rich_text.Text(raw, overflow="fold", no_wrap=False)
            if row == self._cursor_row:
                cursor_col = self._cursor_col
                if cursor_col >= len(raw):
                    text.append(" ")
                    start = len(raw)
                else:
                    start = cursor_col
                text.stylize(CURSOR_STYLE, start, start + 1)
            rendered.extend(
                console.render_lines(
                    text,
                    pad=False,
                    new_lines=False,
                )
            )
        return rendered

    def move_cursor_left(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        if self._cursor_col > 0:
            self._cursor_col -= 1
        elif self._cursor_row > 0:
            self._cursor_row -= 1
            line = self._lines[self._cursor_row]
            self._cursor_col = max(len(line) - 1, 0)
        self._mark_dirty()

    def move_cursor_right(self) -> None:
        if self._cursor_row < 0 or self._cursor_row >= len(self._lines):
            return
        line = self._lines[self._cursor_row]
        last_index = max(len(line) - 1, 0)
        if self._cursor_col < last_index:
            self._cursor_col += 1
        elif self._cursor_row < len(self._lines) - 1:
            self._cursor_row += 1
            line = self._lines[self._cursor_row]
            self._cursor_col = max(len(line) - 1, 0)
        self._mark_dirty()

    def move_cursor_up(self) -> None:
        if self._cursor_row <= 0:
            return
        self._cursor_row -= 1
        line = self._lines[self._cursor_row]
        max_index = max(len(line) - 1, 0)
        self._cursor_col = min(self._cursor_col, max_index)
        self._mark_dirty()

    def move_cursor_down(self) -> None:
        if self._cursor_row >= len(self._lines) - 1:
            return
        self._cursor_row += 1
        line = self._lines[self._cursor_row]
        max_index = max(len(line) - 1, 0)
        self._cursor_col = min(self._cursor_col, max_index)
        self._mark_dirty()

    def insert_char(self, ch: str) -> None:
        if not ch:
            return
        line = self._lines[self._cursor_row]
        col = self._cursor_col
        self._lines[self._cursor_row] = line[:col] + ch + line[col:]
        self._cursor_col = col + len(ch)
        self._mark_dirty()

    def backspace(self) -> None:
        if self._cursor_row == 0 and self._cursor_col == 0:
            return
        line = self._lines[self._cursor_row]
        if self._cursor_col > 0 and line:
            col = self._cursor_col
            self._lines[self._cursor_row] = line[: col - 1] + line[col:]
            self._cursor_col -= 1
        elif self._cursor_row > 0:
            prev_row = self._cursor_row - 1
            prev_line = self._lines[prev_row]
            new_line = prev_line + line
            self._lines[prev_row] = new_line
            del self._lines[self._cursor_row]
            self._cursor_row = prev_row
            self._cursor_col = max(len(new_line) - 1, 0)
        self._mark_dirty()

    def delete(self) -> None:
        line = self._lines[self._cursor_row]
        if line and self._cursor_col < len(line):
            col = self._cursor_col
            new_line = line[:col] + line[col + 1 :]
            self._lines[self._cursor_row] = new_line
            max_index = max(len(new_line) - 1, 0)
            self._cursor_col = min(self._cursor_col, max_index)
        elif not line and self._cursor_row < len(self._lines) - 1:
            next_row = self._cursor_row + 1
            next_line = self._lines[next_row]
            self._lines[self._cursor_row] = line + next_line
            del self._lines[next_row]
            self._cursor_col = max(len(self._lines[self._cursor_row]) - 1, 0)
        self._mark_dirty()

    def break_line(self) -> None:
        line = self._lines[self._cursor_row]
        split_index = self._cursor_col + 1 if line else 0
        split_index = min(split_index, len(line))
        first = line[:split_index]
        second = line[split_index:]
        self._lines[self._cursor_row] = first
        insert_row = self._cursor_row + 1
        self._lines.insert(insert_row, second)
        self._cursor_row = insert_row
        line = self._lines[self._cursor_row]
        self._cursor_col = 0 if not line else 0
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
