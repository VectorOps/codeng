from __future__ import annotations

import json
import typing

from rich import console as rich_console
from rich import syntax as rich_syntax
from rich import text as rich_text

from vocode import state as vocode_state
from vocode import settings as vocode_settings
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.tcf import render_utils as tcf_render_utils


@tui_tcf.ToolCallFormatterManager.register("apply_patch")
class ApplyPatchToolCallFormatter(tui_tcf.BaseToolCallFormatter):
    show_execution_stats_default = False

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

    def _truncate_lines(self, value: str, *, max_lines: int = 5) -> str:
        lines = value.splitlines()
        if len(lines) <= max_lines:
            return value
        if max_lines <= 1:
            return "..."
        return "\n".join(lines[: max_lines - 1] + ["..."])

    def render(
        self,
        terminal: tui_terminal.Terminal,
        req: typing.Optional[vocode_state.ToolCallReq],
        resp: typing.Optional[vocode_state.ToolCallResp],
        context: tui_tcf.ToolCallRenderContext,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        tool_name = "apply_patch"
        arguments: typing.Any = None
        result: typing.Any = None
        if req is not None:
            tool_name = req.name
            arguments = req.arguments
        if resp is not None:
            tool_name = resp.name
            result = resp.result
        display_name = self.format_tool_name(tool_name)
        if config is not None and config.title:
            display_name = config.title

        header = tcf_render_utils.build_tool_line(
            terminal,
            display_name,
            context=context,
            prefix_icon=context.status_icon,
        )

        content_str = ""
        if isinstance(arguments, dict):
            content_str = str(arguments.get("text", ""))
        elif isinstance(arguments, str):
            content_str = arguments
        if req is not None and not content_str and resp is None:
            return header
        if req is not None and content_str:
            body = rich_syntax.Syntax(content_str, "diff")
            return rich_console.Group(header, body)

        result_text, is_error = self._extract_text(result)
        if not result_text.strip():
            return header
        result_text = self._truncate_lines(result_text)
        body = rich_text.Text(result_text, no_wrap=False)
        if is_error:
            body.stylize("red")
        return rich_console.Group(header, body)
