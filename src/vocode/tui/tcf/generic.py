from __future__ import annotations

import json
import typing

from rich import text as rich_text

from vocode import settings as vocode_settings
from vocode.tui import styles as tui_styles
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import terminal as tui_terminal


def _to_single_line(value: str) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def _stringify_value(value: typing.Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, (bool, int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


@tui_tcf.ToolCallFormatterManager.register("generic")
class GenericToolCallFormatter(tui_tcf.BaseToolCallFormatter):
    def _get_int_option(
        self,
        config: vocode_settings.ToolCallFormatter | None,
        key: str,
        default: int,
    ) -> int:
        if config is None:
            return default
        raw = (config.options or {}).get(key)
        if isinstance(raw, int):
            return raw
        return default

    def _get_bool_option(
        self,
        config: vocode_settings.ToolCallFormatter | None,
        key: str,
        default: bool,
    ) -> bool:
        if config is None:
            return default
        raw = (config.options or {}).get(key)
        if isinstance(raw, bool):
            return raw
        return default

    def _max_width(self, terminal: tui_terminal.Terminal) -> int:
        width = terminal.console.size.width
        if width <= 0:
            return 80
        return width

    def format_input(
        self,
        terminal: tui_terminal.Terminal,
        tool_name: str,
        arguments: typing.Any,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        display_name = tool_name
        if config is not None and config.title:
            display_name = config.title

        max_pairs = self._get_int_option(config, "max_pairs", 3)
        max_value_chars = self._get_int_option(config, "max_value_chars", 40)

        text = rich_text.Text(no_wrap=True)
        text.append(display_name, style=tui_styles.TOOL_CALL_NAME_STYLE)

        pairs: list[tuple[str, typing.Any]] = []
        if isinstance(arguments, dict):
            for k, v in arguments.items():
                pairs.append((str(k), v))
        else:
            pairs.append(("args", arguments))

        used = 0
        for k, v in pairs:
            if used >= max_pairs:
                break

            rendered_v = _to_single_line(_stringify_value(v))
            if len(rendered_v) > max_value_chars:
                rendered_v = rendered_v[: max_value_chars - 3] + "..."

            text.append(" ")
            text.append(k, style=tui_styles.TOOL_CALL_KV_KEY_STYLE)
            text.append("=", style=tui_styles.TOOL_CALL_KV_EQ_STYLE)
            text.append(rendered_v, style=tui_styles.TOOL_CALL_KV_VALUE_STYLE)
            used += 1

        if len(pairs) > used:
            text.append(" ")
            text.append("...", style=tui_styles.TOOL_CALL_META_STYLE)

        max_width = self._max_width(terminal)
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
        if config is None:
            return None
        if not config.show_output and not self._get_bool_option(
            config, "format_output", False
        ):
            return None

        display_name = tool_name
        if config.title:
            display_name = config.title

        max_value_chars = self._get_int_option(config, "max_output_chars", 80)

        rendered = _to_single_line(_stringify_value(result))
        if len(rendered) > max_value_chars:
            rendered = rendered[: max_value_chars - 3] + "..."

        text = rich_text.Text(no_wrap=True)
        text.append(display_name, style=tui_styles.TOOL_CALL_NAME_STYLE)
        text.append(" => ", style=tui_styles.TOOL_CALL_META_STYLE)
        text.append(rendered, style=tui_styles.TOOL_CALL_KV_VALUE_STYLE)

        max_width = self._max_width(terminal)
        if max_width > 0:
            text.truncate(max_width, overflow="ellipsis")
        return text
