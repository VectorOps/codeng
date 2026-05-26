from __future__ import annotations

import bisect
import datetime
import typing

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
        self._formatted_entries: list[str] = []
        self._entry_line_counts: list[int] = []
        self._entry_line_starts: list[int] = []
        self._wrapped_lines_by_entry: dict[int, list[str]] = {}
        self._wrapped_width: int | None = None
        self._total_lines_count: int = 0
        self.refresh_data()

    def refresh_data(self) -> None:
        width = max(self._terminal.console.size.width, 1)
        if self._wrapped_width != width:
            self._wrapped_lines_by_entry = {}
            self._wrapped_width = width

        formatted_entries: list[str] = []
        entry_line_counts: list[int] = []
        entry_line_starts: list[int] = []
        total_lines = 0
        for entry in self._entries:
            timestamp = datetime.datetime.fromtimestamp(entry.created).strftime(
                "%H:%M:%S"
            )
            raw = f"{timestamp} {entry.level_name:8s} {entry.logger_name}: {entry.message}"
            formatted_entries.append(raw)
            entry_line_starts.append(total_lines)
            line_count = self._count_wrapped_lines(raw, width)
            entry_line_counts.append(line_count)
            total_lines += line_count

        self._formatted_entries = formatted_entries
        self._entry_line_counts = entry_line_counts
        self._entry_line_starts = entry_line_starts
        self._total_lines_count = total_lines or 1

    def _count_wrapped_lines(self, text: str, width: int) -> int:
        total = 0
        physical_lines = text.splitlines() or [""]
        for physical_line in physical_lines:
            line_length = len(physical_line)
            if line_length <= 0:
                total += 1
                continue
            total += ((line_length - 1) // width) + 1
        return total

    def _wrap_entry(self, index: int) -> list[str]:
        cached = self._wrapped_lines_by_entry.get(index)
        if cached is not None:
            return cached

        width = max(self._wrapped_width or self._terminal.console.size.width, 1)
        text = self._formatted_entries[index]
        wrapped: list[str] = []
        physical_lines = text.splitlines() or [""]
        for physical_line in physical_lines:
            if not physical_line:
                wrapped.append("")
                continue
            start = 0
            while start < len(physical_line):
                wrapped.append(physical_line[start : start + width])
                start += width
        if not wrapped:
            wrapped = [""]
        self._wrapped_lines_by_entry[index] = wrapped
        return wrapped

    def _get_view_lines(self, top_line: int, height: int) -> tuple[list[str], int]:
        total = self._total_lines_count
        if total <= 0 or height <= 0:
            return ([], total)
        if top_line < 0:
            top_line = 0
        if top_line >= total:
            return ([], total)

        entry_index = bisect.bisect_right(self._entry_line_starts, top_line) - 1
        if entry_index < 0:
            entry_index = 0

        lines: list[str] = []
        current_line = top_line
        while entry_index < len(self._formatted_entries) and len(lines) < height:
            entry_start = self._entry_line_starts[entry_index]
            wrapped_entry = self._wrap_entry(entry_index)
            offset = current_line - entry_start
            if offset < 0:
                offset = 0
            remaining = height - len(lines)
            lines.extend(wrapped_entry[offset : offset + remaining])
            entry_index += 1
            current_line = (
                self._entry_line_starts[entry_index]
                if entry_index < len(self._entry_line_starts)
                else total
            )
        return (lines, total)


if typing.TYPE_CHECKING:
    from vocode.tui.app import App
