# src/vocode/tui/lib/components/step_output_component.py
from __future__ import annotations

import typing

from rich import console as rich_console

from vocode import models as vocode_models
from vocode.tui.lib import base as tui_base
from vocode.tui.lib.components import markdown_component as tui_markdown_component
from vocode.tui.lib.components import rich_text_component as tui_rich_text_component


class StepOutputComponent(tui_base.Component):
    def __init__(
        self,
        text: str = "",
        content_type: vocode_models.StepContentType = vocode_models.StepContentType.MARKDOWN,
        compact_lines: int = 10,
        collapsed: bool | None = False,
        id: str | None = None,
        component_style: tui_base.ComponentStyle | None = None,
        markdown_render_mode: tui_markdown_component.MarkdownRenderMode = tui_markdown_component.MarkdownRenderMode.RICH_MARKDOWN,
    ) -> None:
        super().__init__(id=id, component_style=component_style)
        self._text = ""
        self._content_type = content_type
        self.compact_lines = compact_lines
        self._collapsed = collapsed
        self._markdown_render_mode = markdown_render_mode
        self.set_value(text=text, content_type=content_type)

    @property
    def text(self) -> str:
        return self._text

    @property
    def content_type(self) -> vocode_models.StepContentType:
        return self._content_type

    @property
    def markdown_render_mode(self) -> tui_markdown_component.MarkdownRenderMode:
        return self._markdown_render_mode

    @markdown_render_mode.setter
    def markdown_render_mode(
        self, value: tui_markdown_component.MarkdownRenderMode
    ) -> None:
        if value == self._markdown_render_mode:
            return
        self._markdown_render_mode = value
        self._mark_dirty()

    def set_value(self, *, text: str, content_type: vocode_models.StepContentType) -> None:
        normalized = text
        if content_type is vocode_models.StepContentType.MARKDOWN:
            normalized = text.strip()
        if normalized == self._text and content_type == self._content_type:
            return
        self._text = normalized
        self._content_type = content_type
        self._mark_dirty()

    def render(self, options: rich_console.ConsoleOptions) -> tui_base.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        if self._text == "":
            return []

        if self._content_type is vocode_models.StepContentType.RAW:
            inner = tui_rich_text_component.RichTextComponent(
                self._text,
                markup=False,
                ansi=True,
                compact_lines=self.compact_lines,
                component_style=self.component_style,
            )
            inner.terminal = terminal
            inner.set_collapsed(self.is_collapsed)
            return inner.render(options)

        inner = tui_markdown_component.MarkdownComponent(
            self._text,
            compact_lines=self.compact_lines,
            collapsed=self._collapsed,
            component_style=self.component_style,
            render_mode=self._markdown_render_mode,
        )
        inner.terminal = terminal
        return inner.render(options)