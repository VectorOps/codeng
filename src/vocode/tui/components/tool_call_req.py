from __future__ import annotations

import datetime
import json
import typing

from rich import console as rich_console


from vocode import state as vocode_state
from vocode.tui import lib as tui_terminal
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import compact as tui_compact
from vocode.tui.lib import unicode as tui_unicode
from vocode.tui.lib.components import renderable as renderable_component


class ToolCallReqComponent(renderable_component.RenderableComponentBase):
    _COMPACT_LINES: Final[int] = 10
    _STATUS_TEXT: Final[dict[vocode_state.ToolCallReqStatus, str]] = {
        vocode_state.ToolCallReqStatus.REQUIRES_CONFIRMATION: "Waiting for confirmation",
        vocode_state.ToolCallReqStatus.PENDING_EXECUTION: "Pending execution",
        vocode_state.ToolCallReqStatus.EXECUTING: "Running...",
        vocode_state.ToolCallReqStatus.REJECTED: "Rejected",
    }

    def __init__(
        self,
        step: vocode_state.Step,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        super().__init__(
            id=str(step.id),
            component_style=component_style,
        )
        self._step = step
        self._frame_index = 0
        self._animated = False
        self._collapsed = True
        self._show_execution_stats: bool = True

    @property
    def step(self) -> vocode_state.Step:
        return self._step

    def set_step(self, step: vocode_state.Step) -> None:
        self._step = step
        self._mark_dirty()

    @property
    def show_execution_stats(self) -> bool:
        return self._show_execution_stats

    def set_show_execution_stats(self, value: bool) -> None:
        if self._show_execution_stats == value:
            return
        self._show_execution_stats = value
        self._mark_dirty()

    def _compute_overall_status(
        self,
    ) -> vocode_state.ToolCallReqStatus | None:
        message = self._step.message
        if message is None:
            return None

        statuses: list[vocode_state.ToolCallReqStatus] = []
        for tool_call in message.tool_call_requests:
            status = tool_call.status
            if status is None:
                continue
            statuses.append(status)
        if not statuses:
            return None
        for status in statuses:
            if status is vocode_state.ToolCallReqStatus.EXECUTING:
                return status
        return statuses[0]

    def _update_animation(
        self,
        status: vocode_state.ToolCallReqStatus | None,
    ) -> None:
        terminal = self.terminal
        if terminal is None:
            return
        should_animate = (
            self._show_execution_stats
            and status is vocode_state.ToolCallReqStatus.EXECUTING
        )
        if should_animate == self._animated:
            return
        self._animated = should_animate
        if should_animate:
            terminal.register_animation(self)
        else:
            terminal.deregister_animation(self)

    def _render_status_emoji(
        self,
        _: rich_console.Console,
        status: vocode_state.ToolCallReqStatus | None,
    ) -> str:
        terminal = self.terminal
        if terminal is None:
            return ""
        icon = tui_tcf.render_utils.render_status_icon(
            terminal,
            status,
            frame_index=self._frame_index,
            animate_running=self._show_execution_stats,
        )
        if (
            status is vocode_state.ToolCallReqStatus.EXECUTING
            and self._show_execution_stats
        ):
            frames = uni.spinner_frames(tui_unicode.SpinnerVariant.BRAILLE)
            self._frame_index = (self._frame_index + 1) % len(frames)
            return icon
        self._frame_index = 0
        return icon

    def _compute_duration(self) -> datetime.timedelta | None:
        message = self._step.message
        if message is None:
            return None

        created_times: list[datetime.datetime] = []
        handled_times: list[datetime.datetime] = []
        for tool_call in message.tool_call_requests:
            handled_at = tool_call.handled_at
            if handled_at is None:
                continue
            created_at = tool_call.created_at
            if handled_at < created_at:
                continue
            created_times.append(created_at)
            handled_times.append(handled_at)

        if not created_times or not handled_times:
            return None

        return max(handled_times) - min(created_times)

    def _resolve_tool_call_status(
        self,
        tool_call: vocode_state.ToolCallReq,
        response: vocode_state.ToolCallResp | None,
        fallback_status: vocode_state.ToolCallReqStatus | None,
    ) -> vocode_state.ToolCallReqStatus | None:
        status = tool_call.status or fallback_status
        if response is None:
            return status
        if response.status is vocode_state.ToolCallStatus.COMPLETED:
            return vocode_state.ToolCallReqStatus.COMPLETE
        if response.status is vocode_state.ToolCallStatus.REJECTED:
            return vocode_state.ToolCallReqStatus.REJECTED
        return status

    def _build_renderable(
        self,
        console: rich_console.Console,
    ) -> tui_base.Renderable:
        status = self._compute_overall_status()
        self._update_animation(status)

        terminal = self.terminal
        message = self._step.message
        if terminal is None or message is None:
            return ""

        responses_by_id = {
            response.id: response for response in message.tool_call_responses
        }
        manager = tui_tcf.ToolCallFormatterManager.instance()
        renderables: list[rich_console.RenderableType] = []
        duration = self._compute_duration()
        for tool_call in message.tool_call_requests:
            response = responses_by_id.get(tool_call.id)
            tool_status = self._resolve_tool_call_status(tool_call, response, status)
            context = tui_tcf.ToolCallRenderContext(
                status=tool_status,
                duration=duration,
                status_icon=self._render_status_emoji(console, tool_status),
                max_width=console.size.width,
                collapsed=self.is_collapsed,
                show_execution_stats=self._show_execution_stats,
            )
            rendered = manager.render_tool_call(
                terminal=terminal,
                req=tool_call,
                resp=response,
                context=context,
            )
            if rendered is not None:
                renderables.append(rendered)

        if not renderables:
            return ""
        if len(renderables) == 1:
            return renderables[0]
        return rich_console.Group(*renderables)

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
