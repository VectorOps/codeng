from __future__ import annotations

import json
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
    def _try_parse_json(self, value: str) -> typing.Any:
        stripped = value.strip()
        if not stripped:
            return value
        if stripped[0] not in ("{", "["):
            return value
        try:
            return json.loads(stripped)
        except Exception:
            return value

    def _extract_text(self, result: typing.Any, *, _depth: int = 0) -> tuple[str, bool]:
        if _depth >= 3:
            return str(result), False

        if result is None:
            return "", False

        if isinstance(result, str):
            parsed = self._try_parse_json(result)
            if parsed is not result:
                return self._extract_text(parsed, _depth=_depth + 1)
            return result, False

        if isinstance(result, list):
            for item in result:
                text, is_error = self._extract_text(item, _depth=_depth + 1)
                if text.strip():
                    return text, is_error
            try:
                return json.dumps(result, ensure_ascii=False), False
            except Exception:
                return str(result), False

        if isinstance(result, dict):
            error_value = result.get("error")
            if isinstance(error_value, str) and error_value.strip():
                parsed_error = self._try_parse_json(error_value)
                if parsed_error is not error_value:
                    return self._extract_text(parsed_error, _depth=_depth + 1)
                return error_value, True

            for key in ("message", "summary", "text", "output"):
                value = result.get(key)
                if value is None:
                    continue
                if isinstance(value, str):
                    parsed_value = self._try_parse_json(value)
                    if parsed_value is not value:
                        return self._extract_text(parsed_value, _depth=_depth + 1)
                    return value, False
                return self._extract_text(value, _depth=_depth + 1)

            try:
                return json.dumps(result, ensure_ascii=False), False
            except Exception:
                return str(result), False

        return str(result), False

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

        result_text, is_error = self._extract_text(result)
        body = rich_text.Text(result_text, no_wrap=False)
        if is_error:
            body.stylize("red")

        return rich_console.Group(header, body)
