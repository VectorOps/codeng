from __future__ import annotations

import typing

from rich import console as rich_console
from rich import segment as rich_segment
from rich import text as rich_text

from vocode.tui.lib import base as tui_base


class RichTextComponent(tui_base.Component):
    def __init__(
        self,
        text: str = "",
        markup: bool = True,
        ansi: bool = False,
        compact_lines: int = 10,
        id: str | None = None,
        component_style: tui_base.ComponentStyle | None = None,
    ) -> None:
        super().__init__(
            id=id,
            component_style=component_style,
        )
        self._text = text
        self._markup = markup
        self._ansi = ansi
        self.compact_lines = compact_lines
        self._collapsed = False

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        if value == self._text:
            return
        self._text = value
        self._mark_dirty()

    def set_text(self, text: str) -> None:
        self.text = text

    def render(self, options: rich_console.ConsoleOptions) -> tui_base.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        console = terminal.console
        renderable: tui_base.Renderable
        if self._ansi:
            renderable = rich_text.Text.from_ansi(self._text + ANSI_RESET)
        elif self._markup:
            renderable = rich_text.Text.from_markup(self._text)
        else:
            renderable = rich_text.Text(self._text)
        styled = self.apply_style(renderable)
        rendered = console.render_lines(
            styled,
            options=options,
            pad=False,
            new_lines=False,
        )
        lines = typing.cast(tui_base.Lines, rendered)
        compacted = self._maybe_compact_rendered_lines(lines, self.compact_lines)
        if self._ansi and compacted:
            compacted[-1].append(rich_segment.Segment(ANSI_RESET))
        return compacted


ANSI_RESET: typing.Final[str] = "\x1b[0m"
