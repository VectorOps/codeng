from __future__ import annotations

import abc
import re
import typing

from rich import console as rich_console
from rich import control as rich_control
from rich import text as rich_text

from vocode.tui.lib import controls as tui_controls
from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.lib.input import base as input_base


class BaseViewerScreen(abc.ABC):
    def __init__(
        self,
        terminal: tui_terminal.Terminal,
        enable_search: bool = True,
    ) -> None:
        self._terminal: tui_terminal.Terminal = terminal
        self._top_line: int = 0
        self._cursor_line: int = 0
        self._enable_search: bool = enable_search
        self._search_pattern: re.Pattern[str] | None = None
        self._last_search: str | None = None
        self._search_mode: bool = False
        self._search_buffer: str = ""
        self._last_width: int | None = None
        self._first_render: bool = True

    @abc.abstractmethod
    def _get_view_lines(self, top_line: int, height: int) -> tuple[list[str], int]:
        raise NotImplementedError

    def refresh_data(self) -> None:
        return None

    def _height(self) -> int:
        return max(self._terminal.console.size.height - 3, 0)

    def _total_lines(self) -> int:
        _, total = self._get_view_lines(0, 0)
        return total

    def _get_line(self, index: int) -> str:
        lines, _ = self._get_view_lines(index, 1)
        if not lines:
            return ""
        return lines[0]

    def _clamp_state(self) -> None:
        height = self._height()
        total = self._total_lines()
        max_top = max(total - height, 0)
        if self._top_line > max_top:
            self._top_line = max_top
        if self._top_line < 0:
            self._top_line = 0
        if self._cursor_line < 0:
            self._cursor_line = 0
        if total > 0 and self._cursor_line >= total:
            self._cursor_line = total - 1

    def _move_cursor(self, delta: int) -> None:
        total = self._total_lines()
        if total <= 0:
            return
        self._cursor_line += delta
        if self._cursor_line < 0:
            self._cursor_line = 0
        if self._cursor_line >= total:
            self._cursor_line = total - 1
        height = self._height()
        if self._cursor_line < self._top_line:
            self._top_line = self._cursor_line
        elif self._cursor_line >= self._top_line + height:
            self._top_line = self._cursor_line - max(height - 1, 0)
        self._clamp_state()

    def _page_move(self, delta_pages: int) -> None:
        height = self._height()
        if height <= 0:
            return
        delta = delta_pages * height
        self._move_cursor(delta)

    def _scroll_lines(self, delta: int) -> None:
        total = self._total_lines()
        if total <= 0:
            return
        self._top_line += delta
        if self._top_line < 0:
            self._top_line = 0
        max_top = max(total - self._height(), 0)
        if self._top_line > max_top:
            self._top_line = max_top

    def _search(self, pattern: str) -> None:
        try:
            compiled = re.compile(pattern)
        except re.error:
            return
        self._search_pattern = compiled
        self._last_search = pattern
        self._search_next()

    def _search_next(self) -> None:
        pattern = self._search_pattern
        if pattern is None:
            return
        n = self._total_lines()
        if n <= 0:
            return
        start = self._cursor_line + 1
        for offset in range(n):
            index = (start + offset) % n
            line = self._get_line(index)
            if pattern.search(line):
                self._cursor_line = index
                height = self._height()
                if self._cursor_line < self._top_line:
                    self._top_line = self._cursor_line
                elif self._cursor_line >= self._top_line + height:
                    self._top_line = self._cursor_line - max(height - 1, 0)
                self._clamp_state()
                return

    def _build_body_lines(self) -> list[rich_text.Text]:
        size = self._terminal.console.size
        width = size.width
        height = max(size.height - 3, 0)
        body_lines: list[rich_text.Text] = []
        raw_lines, _ = self._get_view_lines(self._top_line, height)
        for offset, line in enumerate(raw_lines):
            index = self._top_line + offset
            content = "" if line is None else str(line)
            text = rich_text.Text(content, no_wrap=True, overflow="crop")
            if (
                self._enable_search
                and index == self._cursor_line
                and self._search_pattern is not None
            ):
                for match in self._search_pattern.finditer(line):
                    text.stylize("reverse", match.start(), match.end())
            body_lines.append(text)
        while len(body_lines) < height:
            body_lines.append(rich_text.Text(""))
        return body_lines

    def _build_footer_renderable(self) -> rich_console.RenderableType:
        width = self._terminal.console.size.width
        total = self._total_lines()
        if total <= 0:
            percent = 0
        else:
            percent = int((self._cursor_line + 1) * 100 / max(total, 1))
        status = f"{self._cursor_line + 1}/{total} ({percent}%)"
        base_help = "q: quit  j/k, up/down: line  f/b, pgdn/pgup, space: page"
        if self._enable_search:
            help_text = f"{base_help}  /: search  n: next"
        else:
            help_text = base_help
        separator = (
            rich_text.Text("-" * width, style="dim")
            if width > 0
            else rich_text.Text("")
        )
        nav = rich_text.Text(help_text, style="dim")
        if len(nav) > width:
            nav.truncate(width)
        status_text = rich_text.Text(status, style="bold")
        if self._enable_search and self._search_mode:
            prompt = rich_text.Text(f"/{self._search_buffer} ", style="dim")
            status_text = rich_text.Text.assemble(prompt, status_text)
        footer = rich_text.Text("\n").join([separator, nav, status_text])
        footer.stylize("reset", 0, len(footer))
        return footer

    def render(self) -> None:
        console = self._terminal.console
        size = console.size
        height = size.height
        if height <= 0:
            return
        width = size.width
        if self._last_width is None or self._last_width != width:
            self.refresh_data()
            self._last_width = width
        self._clamp_state()
        body_lines = self._build_body_lines()
        footer = self._build_footer_renderable()

        console.control(tui_controls.CustomControl.sync_update_start())
        if self._first_render:
            console.control(
                tui_controls.CustomControl.erase_scrollback(),
                rich_control.Control.clear(),
                rich_control.Control.home(),
            )
            self._first_render = False
        else:
            console.control(rich_control.Control.home())
        for line in body_lines:
            console.control(tui_controls.CustomControl.erase_line_end())
            console.print(line)
        console.print(footer)
        console.control(tui_controls.CustomControl.erase_down())
        console.control(tui_controls.CustomControl.sync_update_end())

    def on_key_event(self, event: input_base.KeyEvent) -> None:
        if event.action != "down":
            return
        key = event.key
        terminal = self._terminal
        if self._enable_search and self._search_mode:
            if key == "enter":
                if self._search_buffer:
                    self._search(self._search_buffer)
                self._search_mode = False
                self._search_buffer = ""
                self.render()
                return
            if key == "backspace":
                if self._search_buffer:
                    self._search_buffer = self._search_buffer[:-1]
                self.render()
                return
            if key == "esc":
                self._search_mode = False
                self._search_buffer = ""
                self.render()
                return
            if event.text is not None and event.text not in ("\n", "\r"):
                self._search_buffer += event.text
                self.render()
            return
        if key == "q" and not event.ctrl and not event.alt:
            terminal.pop_screen()
            return
        if key == "j" or key == "down":
            if self._enable_search:
                self._move_cursor(1)
            else:
                self._scroll_lines(1)
                self._cursor_line = self._top_line
            self.render()
            return
        if key == "k" or key == "up":
            if self._enable_search:
                self._move_cursor(-1)
            else:
                self._scroll_lines(-1)
                self._cursor_line = self._top_line
            self.render()
            return
        if key == "g" and not event.ctrl and not event.alt:
            total = self._total_lines()
            if event.shift:
                if total > 0:
                    self._cursor_line = total - 1
                    self._top_line = max(total - self._height(), 0)
            else:
                self._cursor_line = 0
                self._top_line = 0
            self._clamp_state()
            self.render()
            return
        if key == "f" or key == "page_down":
            if self._enable_search:
                self._page_move(1)
            else:
                delta = self._height()
                if delta > 0:
                    self._scroll_lines(delta)
                    self._cursor_line = self._top_line
            self.render()
            return
        if key == "b" or key == "page_up":
            if self._enable_search:
                self._page_move(-1)
            else:
                delta = self._height()
                if delta > 0:
                    self._scroll_lines(-delta)
                    self._cursor_line = self._top_line
            self.render()
            return
        if key == "d" and event.ctrl:
            self._page_move(1)
            self.render()
            return
        if key == "u" and event.ctrl:
            self._page_move(-1)
            self.render()
            return
        if self._enable_search and key == "/":
            self._search_mode = True
            self._search_buffer = ""
            self.render()
            return
        if key == " " or event.text == " ":
            if self._enable_search:
                self._page_move(1)
            else:
                delta = self._height()
                if delta > 0:
                    self._scroll_lines(delta)
                    self._cursor_line = self._top_line
            self.render()
            return
        if self._enable_search and key == "n":
            self._search_next()
            self.render()


class TextViewerScreen(BaseViewerScreen):
    def __init__(self, terminal: tui_terminal.Terminal, text: str) -> None:
        super().__init__(terminal)
        self._lines: list[str] = text.splitlines() or [""]

    def _get_view_lines(self, top_line: int, height: int) -> tuple[list[str], int]:
        total = len(self._lines)
        if total <= 0 or height <= 0:
            return ([], total)
        if top_line < 0:
            top_line = 0
        if top_line >= total:
            return ([], total)
        end = top_line + height
        if end > total:
            end = total
        return (self._lines[top_line:end], total)
