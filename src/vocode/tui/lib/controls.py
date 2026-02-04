from __future__ import annotations

import typing

from rich import control as rich_control
from rich import segment as rich_segment

SYNC_UPDATE_START: typing.Final[str] = "\x1b[?2026h"
SYNC_UPDATE_END: typing.Final[str] = "\x1b[?2026l"
ERASE_SCROLLBACK: typing.Final[str] = "\x1b[3J"
ERASE_SCREEN: typing.Final[str] = "\x1b[2J"
CURSOR_HOME: typing.Final[str] = "\x1b[H"
FULL_CLEAR: typing.Final[str] = "\x1b[H\x1b[2J\x1b[3J"
CURSOR_COLUMN_1: typing.Final[str] = "\x1b[1G"
ERASE_DOWN: typing.Final[str] = "\x1b[J"
ERASE_LINE_END: typing.Final[str] = "\x1b[K"
CURSOR_PREVIOUS_LINE_FMT: typing.Final[str] = "\x1b[{}F"
ALT_SCREEN_ENTER: typing.Final[str] = "\x1b[?1049h"
ALT_SCREEN_EXIT: typing.Final[str] = "\x1b[?1049l"
BRACKETED_PASTE_ENABLE: typing.Final[str] = "\x1b[?2004h"
BRACKETED_PASTE_DISABLE: typing.Final[str] = "\x1b[?2004l"


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
    def full_clear(cls) -> "CustomControl":
        return cls(FULL_CLEAR)

    @classmethod
    def cursor_column_1(cls) -> "CustomControl":
        return cls(CURSOR_COLUMN_1)

    @classmethod
    def erase_down(cls) -> "CustomControl":
        return cls(ERASE_DOWN)

    @classmethod
    def cursor_previous_line(cls, count: int) -> "CustomControl":
        return cls(CURSOR_PREVIOUS_LINE_FMT.format(count))

    @classmethod
    def erase_line_end(cls) -> "CustomControl":
        return cls(ERASE_LINE_END)

    @classmethod
    def enter_alt_screen(cls) -> "CustomControl":
        return cls(ALT_SCREEN_ENTER)

    @classmethod
    def exit_alt_screen(cls) -> "CustomControl":
        return cls(ALT_SCREEN_EXIT)

    @classmethod
    def enable_bracketed_paste(cls) -> "CustomControl":
        return cls(BRACKETED_PASTE_ENABLE)

    @classmethod
    def disable_bracketed_paste(cls) -> "CustomControl":
        return cls(BRACKETED_PASTE_DISABLE)
