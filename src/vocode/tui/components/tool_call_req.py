from __future__ import annotations

import datetime
import json
from typing import Final

from rich import console as rich_console
from rich import markdown as rich_markdown
from rich import markup as rich_markup
from rich import table as rich_table
from rich import text as rich_text

from vocode import state as vocode_state
from vocode.tui import styles as tui_styles
from vocode.logger import logger
from vocode.tui import lib as tui_terminal
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import spinner as tui_spinner
from vocode.tui.lib.components import renderable as renderable_component


class ToolCallReqComponent(renderable_component.RenderableComponentBase):
    _STATUS_ICON_EMOJI: Final[dict[vocode_state.ToolCallReqStatus, str]] = {
        vocode_state.ToolCallReqStatus.REQUIRES_CONFIRMATION: ":question:",
        vocode_state.ToolCallReqStatus.PENDING_EXECUTION: ":hourglass_not_done:",
        vocode_state.ToolCallReqStatus.REJECTED: ":x:",
        vocode_state.ToolCallReqStatus.COMPLETE: ":white_check_mark:",
    }
    _STATUS_ICON_FALLBACK: Final[dict[vocode_state.ToolCallReqStatus, str]] = {
        vocode_state.ToolCallReqStatus.REQUIRES_CONFIRMATION: "[?]",
        vocode_state.ToolCallReqStatus.PENDING_EXECUTION: "...",
        vocode_state.ToolCallReqStatus.REJECTED: "[x]",
        vocode_state.ToolCallReqStatus.COMPLETE: "[+]",
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

    @property
    def step(self) -> vocode_state.Step:
        return self._step

    def set_step(self, step: vocode_state.Step) -> None:
        self._step = step
        self._mark_dirty()

    @staticmethod
    def format_step_markdown(step: vocode_state.Step) -> str | None:
        message = step.message
        if message is None:
            return None

        parts: list[str] = []
        tool_calls = message.tool_call_requests
        if not tool_calls:
            return None

        statuses: list[vocode_state.ToolCallReqStatus] = []
        for tool_call in tool_calls:
            status = tool_call.status
            if status is None:
                continue
            statuses.append(status)

        status_value: vocode_state.ToolCallReqStatus | None = None
        if statuses:
            if vocode_state.ToolCallReqStatus.EXECUTING in statuses:
                status_value = vocode_state.ToolCallReqStatus.EXECUTING
            else:
                status_value = statuses[0]

        if status_value == vocode_state.ToolCallReqStatus.REQUIRES_CONFIRMATION:
            parts.append("Please confirm the tool call:")
            parts.append("")

        for i, tool_call in enumerate(tool_calls):
            if i > 0:
                parts.append("")
            parts.append(f"**Tool call:** `{tool_call.name}`")
            arguments = tool_call.arguments
            if arguments:
                try:
                    args_str = json.dumps(arguments, indent=2, sort_keys=True)
                except TypeError:
                    args_str = str(arguments)
                parts.append("```json")
                parts.append(args_str)
                parts.append("```")

        if not parts:
            return None

        return "\n".join(parts)

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
        if status is vocode_state.ToolCallReqStatus.EXECUTING:
            # TODO: implement a real heuristic for emoji support instead of always using unicode frames.
            frames = tui_spinner.SPINNER_FRAMES_UNICODE
            frame = frames[self._frame_index]
            self._frame_index = (self._frame_index + 1) % len(frames)
            return frame
        if status is None:
            self._frame_index = 0
            return "[ ]"
        # TODO: implement a real heuristic for emoji support instead of always using emoji icons.
        icons = self._STATUS_ICON_EMOJI
        icon = icons.get(status)
        self._frame_index = 0
        if icon is None:
            return "[ ]"
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

        emoji = self._render_status_emoji(console, status)
        markdown = self.format_step_markdown(self._step)

        table = rich_table.Table.grid(padding=(0, 0))
        table.add_column(justify="center", width=3)
        table.add_column(ratio=1)

        emoji_text = rich_markup.render(emoji)
        if markdown is not None:
            body = rich_markdown.Markdown(markdown)
        else:
            body = rich_text.Text("")
        table.add_row(emoji_text, body)

        if status is vocode_state.ToolCallReqStatus.COMPLETE:
            duration = self._compute_duration()
            if duration is not None:
                duration_str = self._format_duration(duration)
                duration_text = rich_text.Text(
                    f"Completed in {duration_str}",
                    style=tui_styles.TOOL_CALL_DURATION_STYLE,
                )
                table.add_row("", duration_text)

        logger.info("render", t=console.render_lines(table))
        return table
