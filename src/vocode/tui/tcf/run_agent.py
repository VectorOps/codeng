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

    def _to_single_line(self, value: str) -> str:
        if not value:
            return ""
        return " ".join(value.split())

    def _truncate(self, value: str, max_chars: int) -> str:
        if max_chars <= 0:
            return ""
        if len(value) <= max_chars:
            return value
        if max_chars <= 3:
            return value[:max_chars]
        return value[: max_chars - 3] + "..."

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
        prompt = ""
        if isinstance(arguments, dict):
            raw = arguments.get("name")
            if isinstance(raw, str):
                agent_name = raw
            raw_prompt = arguments.get("text")
            if isinstance(raw_prompt, str):
                prompt = raw_prompt

        text = rich_text.Text(no_wrap=True)
        text.append(
            terminal.unicode.glyph(":circle:"),
            style=tui_styles.TOOL_CALL_BULLET_STYLE,
        )
        text.append(" ")
        text.append(display_name, style=tui_styles.TOOL_CALL_NAME_STYLE)

        kvs: list[tuple[str, str]] = []
        if agent_name:
            kvs.append(("name", agent_name))
        if prompt:
            prompt = self._truncate(self._to_single_line(prompt), 80)
            kvs.append(("text", prompt))

        if kvs:
            text.append(" ")

        for i, (k, v) in enumerate(kvs):
            if i > 0:
                text.append(
                    ", ",
                    style=tui_styles.TOOL_CALL_META_STYLE,
                )
            text.append(k, style=tui_styles.TOOL_CALL_KV_KEY_STYLE)
            text.append("=", style=tui_styles.TOOL_CALL_KV_EQ_STYLE)
            text.append(v, style=tui_styles.TOOL_CALL_KV_VALUE_STYLE)

        max_width = terminal.console.size.width
        if max_width > 0:
            text.truncate(max_width, overflow="ellipsis")
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
