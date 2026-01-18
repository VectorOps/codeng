from __future__ import annotations

import typing

from rich import console as rich_console

from vocode.tui.lib import base as tui_base


class RichTextComponent(tui_base.Component):
    def __init__(
        self,
        text: str = "",
        compact_lines: int = 10,
        id: str | None = None,
        component_style: tui_base.ComponentStyle | None = None,
    ) -> None:
        super().__init__(
            id=id,
            component_style=component_style,
        )
        self._text = text
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
        renderable: tui_base.Renderable = self._text
        styled = self.apply_style(renderable)
        rendered = console.render_lines(
            styled,
            options=options,
            pad=False,
            new_lines=False,
        )
        lines = typing.cast(tui_base.Lines, rendered)
        return self._maybe_compact_rendered_lines(lines, self.compact_lines)
