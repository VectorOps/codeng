from __future__ import annotations

import typing

from rich import console as rich_console
from rich import syntax as rich_syntax
from rich import text as rich_text

from vocode import settings as vocode_settings
from vocode.tui import styles as tui_styles
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import terminal as tui_terminal


@tui_tcf.ToolCallFormatterManager.register("apply_patch")
class ApplyPatchToolCallFormatter(tui_tcf.BaseToolCallFormatter):
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

        header = rich_text.Text(no_wrap=True)
        header.append(
            terminal.unicode.glyph(":circle:"),
            style=tui_styles.TOOL_CALL_BULLET_STYLE,
        )
        header.append(" ")
        header.append(display_name, style=tui_styles.TOOL_CALL_NAME_STYLE)

        content_str = ""
        if isinstance(arguments, dict):
            content_str = str(arguments.get("text", ""))
        elif isinstance(arguments, str):
            content_str = arguments

        body = rich_syntax.Syntax(content_str, "diff")

        return rich_console.Group(header, body)

    def format_output(
        self,
        terminal: tui_terminal.Terminal,
        tool_name: str,
        result: typing.Any,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        display_name = self.format_tool_name(tool_name)
        if config is not None and config.title:
            display_name = config.title

        header = rich_text.Text(no_wrap=True)
        header.append(
            terminal.unicode.glyph(":circle:"),
            style=tui_styles.TOOL_CALL_BULLET_STYLE,
        )
        header.append(" ")
        header.append(display_name, style=tui_styles.TOOL_CALL_NAME_STYLE)
        header.append(" => ", style=tui_styles.TOOL_CALL_META_STYLE)

        result_text = ""
        if isinstance(result, dict):
            text_value = result.get("text")
            if isinstance(text_value, str):
                result_text = text_value
            else:
                error_value = result.get("error")
                if isinstance(error_value, str):
                    result_text = error_value
                else:
                    result_text = str(result)
        elif isinstance(result, str):
            result_text = result
        else:
            result_text = str(result)

        body = rich_syntax.Syntax(result_text, "diff")

        return rich_console.Group(header, body)
