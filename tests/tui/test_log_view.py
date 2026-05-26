from __future__ import annotations

import io

from rich.console import Console

from vocode.manager import proto as manager_proto
from vocode.tui.lib.terminal import Terminal
from vocode.tui.screens.log_view import LogViewScreen


def _build_terminal(width: int = 20, height: int = 10) -> Terminal:
    console = Console(
        file=io.StringIO(),
        force_terminal=False,
        width=width,
        height=height,
    )
    return Terminal(console=console)


def _build_entry(message: str) -> manager_proto.LogEntry:
    return manager_proto.LogEntry(
        index=0,
        logger_name="bench",
        level=manager_proto.LogLevel.INFO,
        level_name="INFO",
        message=message,
        created=1710000000.0,
    )


def test_log_view_initial_refresh_is_lazy() -> None:
    terminal = _build_terminal(width=16)
    screen = LogViewScreen(
        app=None,
        terminal=terminal,
        entries=[_build_entry("x" * 80) for _ in range(3)],
    )

    assert screen._wrapped_lines_by_entry == {}
    assert screen._total_lines_count > len(screen._entries)


def test_log_view_wraps_only_requested_entries() -> None:
    terminal = _build_terminal(width=18)
    screen = LogViewScreen(
        app=None,
        terminal=terminal,
        entries=[
            _build_entry("a" * 60),
            _build_entry("b" * 60),
            _build_entry("c" * 60),
        ],
    )

    lines, total = screen._get_view_lines(0, 2)

    assert len(lines) == 2
    assert total == screen._total_lines_count
    assert 0 in screen._wrapped_lines_by_entry
    assert 1 not in screen._wrapped_lines_by_entry
    assert 2 not in screen._wrapped_lines_by_entry


def test_log_view_refresh_invalidates_width_specific_wrap_cache() -> None:
    terminal = _build_terminal(width=24)
    screen = LogViewScreen(
        app=None,
        terminal=terminal,
        entries=[_build_entry("message " * 8)],
    )

    first_lines, _ = screen._get_view_lines(0, 5)
    first_entry_lines = list(screen._wrapped_lines_by_entry[0])

    terminal.console._width = 12
    screen.refresh_data()

    second_lines, _ = screen._get_view_lines(0, 5)

    assert screen._wrapped_width == 12
    assert first_lines != second_lines
    assert first_entry_lines != screen._wrapped_lines_by_entry[0]
