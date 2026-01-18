from __future__ import annotations

import typing

from rich import console as rich_console
from rich import markdown as rich_markdown

from vocode.tui.lib import base as tui_base


class MarkdownComponent(tui_base.Component):
    def __init__(
        self,
        markdown: str = "",
        compact_lines: int = 10,
        collapsed: bool | None = False,
        id: str | None = None,
        component_style: tui_base.ComponentStyle | None = None,
    ) -> None:
        super().__init__(
            id=id,
            component_style=component_style,
        )
        self._markdown = markdown
        self.compact_lines = compact_lines
        self._collapsed = collapsed

    @property
    def markdown(self) -> str:
        return self._markdown

    @markdown.setter
    def markdown(self, value: str) -> None:
        if value == self._markdown:
            return
        self._markdown = value
        self._mark_dirty()

    def set_text(self, text: str) -> None:
        self.markdown = text

    def render(self, options: rich_console.ConsoleOptions) -> tui_base.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        console = terminal.console
        renderable: tui_base.Renderable = rich_markdown.Markdown(self._markdown)
        styled = self.apply_style(renderable)
        rendered = console.render_lines(
            styled,
            options=options,
            pad=False,
            new_lines=False,
        )
        lines = typing.cast(tui_base.Lines, rendered)
        return self._maybe_compact_rendered_lines(lines, self.compact_lines)
