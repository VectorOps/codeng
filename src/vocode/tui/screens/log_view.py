from __future__ import annotations

import datetime
import typing

from rich import segment as rich_segment
from rich import text as rich_text

from vocode.manager import proto as manager_proto
from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.screens import base_viewer


class LogViewScreen(base_viewer.BaseViewerScreen):
    def __init__(
        self,
        app: "App",
        terminal: tui_terminal.Terminal,
        entries: list[manager_proto.LogEntry] | None = None,
    ) -> None:
        super().__init__(terminal, enable_search=False)
        self._app = app
        self._entries: list[manager_proto.LogEntry] = entries or []
        self._lines: list[str] = []
        self.refresh_data()

    def refresh_data(self) -> None:
        console = self._terminal.console
        options = console.options.update(width=console.size.width)
        all_lines: list[str] = []
        for entry in self._entries:
            timestamp = datetime.datetime.fromtimestamp(entry.created).strftime(
                "%H:%M:%S"
            )
            raw = f"{timestamp} {entry.level_name:8s} {entry.logger_name}: {entry.message}"
            text = rich_text.Text(raw, no_wrap=False)
            rendered = console.render_lines(text, options)
            for line in rendered:
                plain = "".join(segment.text for segment in line)
                all_lines.append(plain)
        self._lines = all_lines or [""]

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


if typing.TYPE_CHECKING:
    from vocode.tui.app import App
