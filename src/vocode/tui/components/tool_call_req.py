from __future__ import annotations

import datetime
import json
import typing
from typing import Final

from rich import console as rich_console
from rich import panel as rich_panel
from rich import padding as rich_padding
from rich import text as rich_text
from rich import markup as rich_markup


from vocode import state as vocode_state
from vocode.tui import styles as tui_styles
from vocode.tui import lib as tui_terminal
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import compact as tui_compact
from vocode.tui.lib import unicode as tui_unicode
from vocode.tui.lib.components import renderable as renderable_component


class ToolCallReqComponent(renderable_component.RenderableComponentBase):
    _COMPACT_LINES: Final[int] = 10
    _STATUS_ICON_NAME: Final[dict[vocode_state.ToolCallReqStatus, str]] = {
        vocode_state.ToolCallReqStatus.REQUIRES_CONFIRMATION: "black_question_mark_ornament",
        vocode_state.ToolCallReqStatus.PENDING_EXECUTION: "hourglass_with_flowing_sand",
        vocode_state.ToolCallReqStatus.REJECTED: "heavy_multiplication_x",
        vocode_state.ToolCallReqStatus.COMPLETE: "heavy_check_mark",
    }
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
        should_animate = status is vocode_state.ToolCallReqStatus.EXECUTING
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
        uni = terminal.unicode
        if status is vocode_state.ToolCallReqStatus.EXECUTING:
            frame = uni.spinner_frame(
                self._frame_index,
                tui_unicode.SpinnerVariant.BRAILLE,
            )
            frames = uni.spinner_frames(tui_unicode.SpinnerVariant.BRAILLE)
            self._frame_index = (self._frame_index + 1) % len(frames)
            return frame
        if status is None:
            self._frame_index = 0
            return ""
        icon_name = self._STATUS_ICON_NAME.get(status)
        self._frame_index = 0
        if icon_name is None:
            return ""
        return uni.glyph(icon_name)

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

    @staticmethod
    def _format_duration(duration: datetime.timedelta) -> str:
        total_seconds = int(duration.total_seconds())
        if total_seconds < 1:
            return "< 1s"
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def _build_renderable(
        self,
        console: rich_console.Console,
    ) -> tui_base.Renderable:
        status = self._compute_overall_status()
        self._update_animation(status)

        renderables: list[rich_console.RenderableType] = []
        terminal = self.terminal
        message = self._step.message
        tool_calls: list[vocode_state.ToolCallReq] = []
        tool_responses: list[vocode_state.ToolCallResp] = []
        if message is not None:
            tool_calls = message.tool_call_requests
            tool_responses = message.tool_call_responses

        if (
            tool_calls
            and status is vocode_state.ToolCallReqStatus.REQUIRES_CONFIRMATION
        ):
            renderables.append(rich_text.Text("Please confirm the tool call:"))

        if terminal is not None and (tool_calls or tool_responses):
            manager = tui_tcf.ToolCallFormatterManager.instance()
            for tool_call in tool_calls:
                rendered_req = manager.format_request(terminal, tool_call)
                if rendered_req is not None:
                    renderables.append(rendered_req)
            for resp in tool_responses:
                rendered_resp = manager.format_response(terminal, resp)
                if rendered_resp is not None:
                    renderables.append(rendered_resp)

        if not self._show_execution_stats or status is None:
            if not renderables:
                return ""
            if len(renderables) == 1:
                return renderables[0]
            return rich_console.Group(*renderables)

        icon = self._render_status_emoji(console, status)

        if status is vocode_state.ToolCallReqStatus.COMPLETE:
            duration = self._compute_duration()
            if duration is not None:
                duration_str = self._format_duration(duration)
                status_text = f"Completed in {duration_str}"
            else:
                status_text = "Completed."
        else:
            status_text = self._STATUS_TEXT.get(
                status,
                status.value.replace("_", " ").capitalize(),
            )

        style = tui_styles.TOOL_CALL_DURATION_STYLE
        status_line = rich_markup.render(f"  [{style}]{icon} {status_text}[/]")
        renderables.append(status_line)

        if len(renderables) == 1:
            return renderables[0]

        grouped = rich_console.Group(*renderables)
        return rich_padding.Padding(
            grouped,
            pad=1,
            style=tui_styles.TOOL_CALL_PANEL_STYLE,
        )

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
