from __future__ import annotations

import datetime
import typing
from typing import Final

from rich import console as rich_console

from vocode import state as vocode_state
from vocode.tui import lib as tui_terminal
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import compact as tui_compact
from vocode.tui.lib.components import renderable as renderable_component


class ToolCallRespComponent(renderable_component.RenderableComponentBase):
    _COMPACT_LINES: Final[int] = 10

    def __init__(
        self,
        step: vocode_state.Step,
        tool_call_id: str,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        super().__init__(
            id=f"{step.id}:tool_resp:{tool_call_id}",
            component_style=component_style,
        )
        self._step = step
        self._tool_call_id = tool_call_id
        self._collapsed = True

    def set_step(self, step: vocode_state.Step) -> None:
        self._step = step
        self._mark_dirty()

    def set_hidden(self, hidden: bool) -> None:
        self.is_hidden = hidden

    def _get_response(self) -> vocode_state.ToolCallResp | None:
        message = self._step.message
        if message is None:
            return None
        for response in message.tool_call_responses:
            if response.id == self._tool_call_id:
                return response
        return None

    def _compute_duration(self) -> datetime.timedelta | None:
        message = self._step.message
        if message is None:
            return None
        for tool_call in message.tool_call_requests:
            if tool_call.id != self._tool_call_id:
                continue
            handled_at = tool_call.handled_at
            if handled_at is None:
                return None
            created_at = tool_call.created_at
            if handled_at < created_at:
                return None
            return handled_at - created_at
        return None

    def _build_renderable(
        self,
        console: rich_console.Console,
    ) -> tui_base.Renderable:
        terminal = self.terminal
        response = self._get_response()
        if terminal is None or response is None:
            return ""
        manager = tui_tcf.ToolCallFormatterManager.instance()
        context = tui_tcf.ToolCallRenderContext(
            duration=self._compute_duration(),
            max_width=console.size.width,
            collapsed=self.is_collapsed,
            show_execution_stats=False,
        )
        rendered = manager.render_tool_response(
            terminal=terminal,
            resp=response,
            context=context,
        )
        if rendered is None:
            return ""
        return rendered

    def render(self, options: rich_console.ConsoleOptions) -> tui_base.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        console = terminal.console
        renderable = self._build_renderable(console)
        if renderable == "":
            return []
        styled = self.apply_style(renderable)
        rendered = console.render_lines(
            styled,
            options=options,
            pad=False,
            new_lines=False,
        )
        lines = typing.cast(tui_base.Lines, rendered)
        if self.is_expanded:
            return lines
        return tui_compact.compact_rendered_lines(lines, self._COMPACT_LINES)
