from __future__ import annotations

import typing

from rich import control as rich_control
from rich import segment as rich_segment

SYNC_UPDATE_START: typing.Final[str] = "\x1b[?2026h"
SYNC_UPDATE_END: typing.Final[str] = "\x1b[?2026l"
ERASE_SCROLLBACK: typing.Final[str] = "\x1b[3J"
ERASE_SCREEN: typing.Final[str] = "\x1b[2J"
CURSOR_HOME: typing.Final[str] = "\x1b[H"
CURSOR_COLUMN_1: typing.Final[str] = "\x1b[1G"
ERASE_DOWN: typing.Final[str] = "\x1b[J"
CURSOR_PREVIOUS_LINE_FMT: typing.Final[str] = "\x1b[{}F"


class CustomControl(rich_control.Control):
    __slots__ = ("segment",)

    def __init__(self, text: str) -> None:
        self.segment = rich_segment.Segment(text)

    @classmethod
    def sync_update_start(cls) -> "CustomControl":
        return cls(SYNC_UPDATE_START)

    @classmethod
    def sync_update_end(cls) -> "CustomControl":
        return cls(SYNC_UPDATE_END)

    @classmethod
    def erase_scrollback(cls) -> "CustomControl":
        return cls(ERASE_SCROLLBACK)

    @classmethod
    def cursor_column_1(cls) -> "CustomControl":
        return cls(CURSOR_COLUMN_1)

    @classmethod
    def erase_down(cls) -> "CustomControl":
        return cls(ERASE_DOWN)

    @classmethod
    def cursor_previous_line(cls, count: int) -> "CustomControl":
        return cls(CURSOR_PREVIOUS_LINE_FMT.format(count))
