from __future__ import annotations

import enum
import typing

from rich import console as rich_console
from rich import markdown as rich_markdown
from rich import syntax as rich_syntax

from vocode.tui.lib import base as tui_base


class MarkdownRenderMode(str, enum.Enum):
    RICH_MARKDOWN = "rich_markdown"
    SYNTAX = "syntax"


class MarkdownComponent(tui_base.Component):
    def __init__(
        self,
        markdown: str = "",
        compact_lines: int = 10,
        collapsed: bool | None = False,
        id: str | None = None,
        component_style: tui_base.ComponentStyle | None = None,
        render_mode: MarkdownRenderMode = MarkdownRenderMode.RICH_MARKDOWN,
    ) -> None:
        super().__init__(
            id=id,
            component_style=component_style,
        )
        self._markdown = markdown
        self.compact_lines = compact_lines
        self._collapsed = collapsed
        self._render_mode = render_mode

    @property
    def render_mode(self) -> MarkdownRenderMode:
        return self._render_mode

    @render_mode.setter
    def render_mode(self, value: MarkdownRenderMode) -> None:
        if value == self._render_mode:
            return
        self._render_mode = value
        self._mark_dirty()

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
        if self._markdown == "":
            return []
        console = terminal.console
        if self._render_mode is MarkdownRenderMode.SYNTAX:
            renderable = rich_syntax.Syntax(
                self._markdown,
                "markdown",
                word_wrap=True,
                background_color="default",
            )
        else:
            renderable = rich_markdown.Markdown(self._markdown)
        styled = self.apply_style(renderable)
        rendered = console.render_lines(
            styled,
            options=options,
            pad=False,
            new_lines=False,
        )
        lines = typing.cast(tui_base.Lines, rendered)
        return self._maybe_compact_rendered_lines(lines, self.compact_lines)
