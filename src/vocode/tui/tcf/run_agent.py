from __future__ import annotations

import typing

from rich import text as rich_text

from vocode import state as vocode_state
from vocode import settings as vocode_settings
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.tcf import render_utils as tcf_render_utils


@tui_tcf.ToolCallFormatterManager.register("run_agent")
class RunAgentToolCallFormatter(tui_tcf.BaseToolCallFormatter):
    show_execution_stats_default: bool = False

    def render(
        self,
        terminal: tui_terminal.Terminal,
        req: typing.Optional[vocode_state.ToolCallReq],
        resp: typing.Optional[vocode_state.ToolCallResp],
        context: tui_tcf.ToolCallRenderContext,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        _ = resp
        if req is None:
            return None
        tool_name = req.name
        arguments = req.arguments
        display_name = self.format_tool_name(tool_name)
        if config is not None and config.title:
            display_name = config.title

        agent_name = ""
        prompt = ""
        if isinstance(arguments, dict):
            raw = arguments.get("name")
            if isinstance(raw, str):
                agent_name = raw
            raw_prompt = arguments.get("text")
            if isinstance(raw_prompt, str):
                prompt = raw_prompt

        kvs: list[tuple[str, str]] = []
        if agent_name:
            kvs.append(("name", agent_name))
        if prompt:
            prompt = tcf_render_utils.to_single_line(prompt)
            prompt, _ = tcf_render_utils.truncate_to_width(prompt, 80)
            kvs.append(("text", prompt))

        line = rich_text.Text(no_wrap=True)
        line.append(
            terminal.unicode.glyph(":circle:"),
            style="dim",
        )
        line.append(" ")
        line.append(display_name, style="bold")
        if kvs:
            line.append(" ")
            for index, (key, value) in enumerate(kvs):
                if index > 0:
                    line.append(", ", style="dim")
                line.append(key, style="cyan")
                line.append("=", style="dim")
                line.append(value)
        if context.max_width > 0:
            line.truncate(context.max_width, overflow="ellipsis")
        return line
