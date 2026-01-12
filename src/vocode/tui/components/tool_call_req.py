from __future__ import annotations

import json
from typing import Final

from rich import console as rich_console
from rich import markdown as rich_markdown
from rich import markup as rich_markup
from rich import table as rich_table
from rich import text as rich_text

from vocode import state as vocode_state
from vocode.logger import logger
from vocode.tui import lib as tui_terminal
from vocode.tui.lib import base as tui_base
from vocode.tui.lib.components import renderable as renderable_component


class ToolCallReqComponent(renderable_component.RenderableComponentBase):
    _EXECUTING_FRAMES_UNICODE: Final[tuple[str, ...]] = (
        " ⠋ ",
        " ⠙ ",
        " ⠹ ",
        " ⠸ ",
        " ⠼ ",
        " ⠴ ",
        " ⠦ ",
        " ⠧ ",
        " ⠇ ",
        " ⠏ ",
    )
    _EXECUTING_FRAMES_FALLBACK: Final[tuple[str, ...]] = (" . ", ".. ", "...")
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

        if status_value is vocode_state.ToolCallReqStatus.REQUIRES_CONFIRMATION:
            parts.append("Please confirm the tool call")

        for tool_call in tool_calls:
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
            frames = self._EXECUTING_FRAMES_UNICODE
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
            body: tui_base.Renderable = rich_markdown.Markdown(markdown)
        else:
            body = rich_text.Text("")
        table.add_row(emoji_text, body)
        return table
