from __future__ import annotations

from typing import Final

from rich import console as rich_console
from rich import padding as rich_padding
from rich import segment as rich_segment
from rich import text as rich_text

from vocode import state as vocode_state
from vocode.tui import styles as tui_styles
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib.components import renderable as renderable_component


class ToolCallRespComponent(renderable_component.RenderableComponentBase):
    _MAX_OUTPUT_LINES: Final[int] = 10

    def __init__(
        self,
        step: vocode_state.Step,
        component_style: tui_base.ComponentStyle | None = None,
    ) -> None:
        super().__init__(
            id=str(step.id),
            component_style=component_style,
        )
        self._step = step
        self._collapsed = True

    @property
    def step(self) -> vocode_state.Step:
        return self._step

    def set_step(self, step: vocode_state.Step) -> None:
        self._step = step
        self._mark_dirty()

    def _build_renderable(
        self,
        console: rich_console.Console,
    ) -> tui_base.Renderable:
        terminal = self.terminal
        if terminal is None:
            return ""

        message = self._step.message
        if message is None:
            return ""

        tool_calls = message.tool_call_responses
        if not tool_calls:
            return ""

        manager = tui_tcf.ToolCallFormatterManager.instance()
        rendered_calls: list[rich_console.RenderableType] = []
        for tool_call in tool_calls:
            rendered = manager.format_response(terminal, tool_call)
            if rendered is None:
                continue

            header = rich_text.Text(no_wrap=True)
            header.append(tool_call.name, style=tui_styles.TOOL_CALL_NAME_STYLE)

            content_lines = console.render_lines(
                rendered,
                options=console.options,
                pad=False,
                new_lines=False,
            )
            all_lines = list(content_lines)
            lines = all_lines
            if self.is_collapsed and len(lines) > self._MAX_OUTPUT_LINES:
                lines = lines[: self._MAX_OUTPUT_LINES]
                remaining = len(all_lines) - len(lines)
                if remaining > 0:
                    suffix = rich_text.Text(f"... ({remaining} other lines)")
                    suffix_lines = console.render_lines(
                        suffix,
                        options=console.options,
                        pad=False,
                        new_lines=False,
                    )
                    if suffix_lines:
                        lines.append(suffix_lines[0])

            output = rich_console.Group(
                *(rich_segment.Segments(line) for line in lines)
            )
            block = rich_padding.Padding(
                output,
                pad=(0, 1),
                style=tui_styles.TOOL_CALL_OUTPUT_BLOCK_STYLE,
            )
            rendered_calls.append(rich_console.Group(header, block))

        if not rendered_calls:
            return ""
        if len(rendered_calls) == 1:
            return rendered_calls[0]
        return rich_console.Group(*rendered_calls)
