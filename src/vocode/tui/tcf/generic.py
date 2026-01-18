from __future__ import annotations

import json
import typing
import pydantic

from rich import cells as rich_cells
from rich import text as rich_text

from vocode.logger import logger
from vocode import settings as vocode_settings
from vocode.tui import styles as tui_styles
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import terminal as tui_terminal


class _GenericToolCallFormatterOptions(pydantic.BaseModel):
    max_pairs: int = 6
    max_value_chars: int = 80
    min_value_chars: int = 8
    format_output: bool = False
    max_output_chars: int = 200


def _to_single_line(value: str) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def _cell_len(value: str) -> int:
    return rich_cells.cell_len(value)


def _truncate_to_width(value: str, width: int) -> tuple[str, bool]:
    if width <= 0:
        return "", bool(value)

    if _cell_len(value) <= width:
        return value, False

    if width <= 3:
        return value[:width], True

    return value[: width - 3] + "...", True


def _stringify_value(value: typing.Any, *, _depth: int = 0) -> str:
    if value is None:
        return "null"
    if isinstance(value, (bool, int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        if _depth >= 2:
            try:
                return json.dumps(value, ensure_ascii=False, sort_keys=True)
            except TypeError:
                return str(value)

        max_items = 50
        parts: list[str] = []
        items = list(value)
        for item in items[:max_items]:
            parts.append(_to_single_line(_stringify_value(item, _depth=_depth + 1)))
        rendered = ", ".join(parts)
        if len(items) > max_items:
            rendered = f"{rendered}, ..."
        return rendered

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def _fit_kv_pairs_to_width(
    *,
    max_width: int,
    prefix_len: int,
    display_name_len: int,
    pairs: list[tuple[str, str]],
    min_value_chars: int,
    joiner: str,
) -> tuple[list[tuple[str, str]], bool]:
    if max_width <= 0:
        return pairs, False

    joiner_len = _cell_len(joiner)
    base_len = prefix_len + display_name_len

    if not pairs:
        return [], False

    removed_from_end = False
    shown: list[tuple[str, str]] = list(pairs)

    while True:
        if not shown:
            return [], bool(pairs)

        need_ellipsis = removed_from_end or (len(shown) < len(pairs))
        token_count = len(shown) + (1 if need_ellipsis else 0)

        non_value_len = base_len
        non_value_len += 1
        non_value_len += joiner_len * (token_count - 1)
        non_value_len += sum(_cell_len(k) + 1 for k, _ in shown)
        if need_ellipsis:
            non_value_len += 3

        available_for_values = max_width - non_value_len
        if available_for_values < 0:
            shown = shown[:-1]
            removed_from_end = True
            continue

        full_lens = [_cell_len(v) for _, v in shown]
        min_lens = [
            min(l, min_value_chars) if l > min_value_chars else l for l in full_lens
        ]

        if sum(min_lens) > available_for_values:
            shown = shown[:-1]
            removed_from_end = True
            continue

        excess = sum(full_lens) - available_for_values
        if excess <= 0:
            return shown, need_ellipsis

        reducible = [full - mn for full, mn in zip(full_lens, min_lens)]
        total_reducible = sum(reducible)
        if total_reducible <= 0:
            shown = shown[:-1]
            removed_from_end = True
            continue

        reductions = [0 for _ in shown]
        used = 0
        for i, r in enumerate(reducible):
            if r <= 0:
                continue
            cut = int(excess * (r / total_reducible))
            cut = min(cut, r)
            reductions[i] = cut
            used += cut

        remaining = excess - used
        if remaining > 0:
            order = sorted(range(len(shown)), key=lambda i: reducible[i], reverse=True)
            for i in order:
                if remaining <= 0:
                    break
                cap = reducible[i] - reductions[i]
                if cap <= 0:
                    continue
                take = min(cap, remaining)
                reductions[i] += take
                remaining -= take

        targets = [full - cut for full, cut in zip(full_lens, reductions)]
        targets = [max(t, mn) for t, mn in zip(targets, min_lens)]

        truncated: list[tuple[str, str]] = []
        for (k, v), target in zip(shown, targets):
            v2, _ = _truncate_to_width(v, target)
            truncated.append((k, v2))
        return truncated, need_ellipsis


@tui_tcf.ToolCallFormatterManager.register("generic")
class GenericToolCallFormatter(tui_tcf.BaseToolCallFormatter):
    def _parse_options(
        self, config: vocode_settings.ToolCallFormatter | None
    ) -> tuple[_GenericToolCallFormatterOptions, str | None]:
        if config is None:
            return _GenericToolCallFormatterOptions(), None
        try:
            return (
                _GenericToolCallFormatterOptions.model_validate(config.options or {}),
                None,
            )
        except pydantic.ValidationError as e:
            msg = _to_single_line(str(e))
            msg, _ = _truncate_to_width(msg, 120)
            return _GenericToolCallFormatterOptions(), msg

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
        logger.info("FORMATTING")

        display_name = tool_name
        if config is not None and config.title:
            display_name = config.title
        options, options_error = self._parse_options(config)
        max_pairs = options.max_pairs
        max_value_chars = options.max_value_chars
        min_value_chars = options.min_value_chars

        text = rich_text.Text(no_wrap=True)
        text.append("<<< ", style=tui_styles.TOOL_CALL_BULLET_STYLE)
        text.append(display_name, style=tui_styles.TOOL_CALL_NAME_STYLE)
        if options_error is not None:
            text.append(" ", style=tui_styles.TOOL_CALL_META_STYLE)
            text.append(
                f"[invalid formatter options; using defaults: {options_error}]",
                style=tui_styles.TOOL_CALL_META_STYLE,
            )

        raw_pairs: list[tuple[str, typing.Any]] = []
        if isinstance(arguments, dict):
            for k, v in arguments.items():
                raw_pairs.append((str(k), v))
        else:
            raw_pairs.append(("args", arguments))

        if max_pairs > 0:
            raw_pairs = raw_pairs[:max_pairs]

        rendered_pairs: list[tuple[str, str]] = []
        for k, v in raw_pairs:
            rendered_v = _to_single_line(_stringify_value(v))
            if _cell_len(rendered_v) > max_value_chars:
                rendered_v, _ = _truncate_to_width(rendered_v, max_value_chars)
            rendered_pairs.append((k, rendered_v))

        joiner = ", "
        max_width = self._max_width(terminal)
        prefix_len = _cell_len("<<< ")
        fitted_pairs, need_ellipsis = _fit_kv_pairs_to_width(
            max_width=max_width,
            prefix_len=prefix_len,
            display_name_len=_cell_len(display_name),
            pairs=rendered_pairs,
            min_value_chars=min_value_chars,
            joiner=joiner,
        )

        if fitted_pairs:
            text.append(" ")

        for i, (k, rendered_v) in enumerate(fitted_pairs):
            if i > 0:
                text.append(joiner, style=tui_styles.TOOL_CALL_META_STYLE)
            text.append(k, style=tui_styles.TOOL_CALL_KV_KEY_STYLE)
            text.append("=", style=tui_styles.TOOL_CALL_KV_EQ_STYLE)
            text.append(rendered_v, style=tui_styles.TOOL_CALL_KV_VALUE_STYLE)

        if need_ellipsis:
            if fitted_pairs:
                text.append(joiner, style=tui_styles.TOOL_CALL_META_STYLE)
            else:
                text.append(" ", style=tui_styles.TOOL_CALL_META_STYLE)
            text.append("...", style=tui_styles.TOOL_CALL_META_STYLE)

        max_width = self._max_width(terminal)
        if max_width > 0:
            text.truncate(max_width, overflow="ellipsis")

        logger.info("print", text=text)

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
        options, options_error = self._parse_options(config)
        if not config.show_output and not options.format_output:
            return None

        display_name = tool_name
        if config.title:
            display_name = config.title
        max_value_chars = options.max_output_chars

        rendered = _to_single_line(_stringify_value(result))
        if len(rendered) > max_value_chars:
            rendered = rendered[: max_value_chars - 3] + "..."

        text = rich_text.Text(no_wrap=True)
        text.append(">>> ", style=tui_styles.TOOL_CALL_BULLET_STYLE)
        text.append(display_name, style=tui_styles.TOOL_CALL_NAME_STYLE)
        if options_error is not None:
            text.append(" ", style=tui_styles.TOOL_CALL_META_STYLE)
            text.append(
                f"[invalid formatter options; using defaults: {options_error}]",
                style=tui_styles.TOOL_CALL_META_STYLE,
            )
        text.append(" => ", style=tui_styles.TOOL_CALL_META_STYLE)
        text.append(rendered, style=tui_styles.TOOL_CALL_KV_VALUE_STYLE)

        max_width = self._max_width(terminal)
        if max_width > 0:
            text.truncate(max_width, overflow="ellipsis")

        return text
