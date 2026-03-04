from __future__ import annotations

import typing

from rich import text as rich_text

from vocode import settings as vocode_settings
from vocode.tui import styles as tui_styles
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import terminal as tui_terminal


@tui_tcf.ToolCallFormatterManager.register("run_agent")
class RunAgentToolCallFormatter(tui_tcf.BaseToolCallFormatter):
    show_execution_stats_default: bool = False

    def format_input(
        self,
        terminal: tui_terminal.Terminal,
        tool_name: str,
        arguments: typing.Any,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        display_name = self.format_tool_name(tool_name)
        if config is not None and config.title:
            display_name = config.title

        agent_name = ""
        if isinstance(arguments, dict):
            raw = arguments.get("name")
            if isinstance(raw, str):
                agent_name = raw

        text = rich_text.Text(no_wrap=True)
        text.append(
            terminal.unicode.glyph(":circle:"),
            style=tui_styles.TOOL_CALL_BULLET_STYLE,
        )
        text.append(" ")
        text.append(display_name, style=tui_styles.TOOL_CALL_NAME_STYLE)
        if agent_name:
            text.append(" ")
            text.append("name=", style=tui_styles.TOOL_CALL_KV_EQ_STYLE)
            text.append(agent_name, style=tui_styles.TOOL_CALL_KV_VALUE_STYLE)
        return text

    def format_output(
        self,
        terminal: tui_terminal.Terminal,
        tool_name: str,
        result: typing.Any,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        _ = terminal
        _ = tool_name
        _ = result
        _ = config
        return None