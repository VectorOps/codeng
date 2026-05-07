from __future__ import annotations

import datetime
import json
import typing
from typing import Final

from rich import cells as rich_cells
from rich import text as rich_text

from vocode import state as vocode_state
from vocode.tui import styles as tui_styles
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.lib import unicode as tui_unicode


_STATUS_ICON_NAME: Final[dict[vocode_state.ToolCallReqStatus, str]] = {
    vocode_state.ToolCallReqStatus.REQUIRES_CONFIRMATION: "black_question_mark_ornament",
    vocode_state.ToolCallReqStatus.PENDING_EXECUTION: "hourglass_with_flowing_sand",
    vocode_state.ToolCallReqStatus.COMPLETE: "heavy_check_mark",
}


def to_single_line(value: str) -> str:
    if not value:
        return ""
    return " ".join(value.split())


def cell_len(value: str) -> int:
    return rich_cells.cell_len(value)


def truncate_to_width(value: str, width: int) -> tuple[str, bool]:
    if width <= 0:
        return "", bool(value)

    if cell_len(value) <= width:
        return value, False

    if width <= 3:
        return value[:width], True

    return value[: width - 3] + "...", True


def stringify_value(value: typing.Any, *, depth: int = 0) -> str:
    if value is None:
        return "null"
    if isinstance(value, (bool, int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        if depth >= 2:
            try:
                return json.dumps(value, ensure_ascii=False, sort_keys=True)
            except TypeError:
                return str(value)

        parts: list[str] = []
        items = list(value)
        for item in items[:50]:
            parts.append(to_single_line(stringify_value(item, depth=depth + 1)))
        rendered = ", ".join(parts)
        if len(items) > 50:
            rendered = f"{rendered}, ..."
        return rendered

    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


def fit_kv_pairs_to_width(
    *,
    max_width: int,
    prefix_len: int,
    display_name_len: int,
    suffix_len: int,
    pairs: list[tuple[str, str]],
    min_value_chars: int,
    joiner: str,
) -> tuple[list[tuple[str, str]], bool]:
    if max_width <= 0:
        return pairs, False

    joiner_len = cell_len(joiner)
    base_len = prefix_len + display_name_len + suffix_len

    if not pairs:
        return [], False

    removed_from_end = False
    shown: list[tuple[str, str]] = list(pairs)

    while True:
        if not shown:
            return [], bool(pairs)

        need_ellipsis = removed_from_end or (len(shown) < len(pairs))
        token_count = len(shown) + (1 if need_ellipsis else 0)

        non_value_len = base_len + 1
        non_value_len += joiner_len * (token_count - 1)
        non_value_len += sum(cell_len(k) + 1 for k, _ in shown)
        if need_ellipsis:
            non_value_len += 3

        available_for_values = max_width - non_value_len
        if available_for_values < 0:
            shown = shown[:-1]
            removed_from_end = True
            continue

        full_lens = [cell_len(v) for _, v in shown]
        min_lens = [
            min(length, min_value_chars) if length > min_value_chars else length
            for length in full_lens
        ]

        if sum(min_lens) > available_for_values:
            shown = shown[:-1]
            removed_from_end = True
            continue

        excess = sum(full_lens) - available_for_values
        if excess <= 0:
            return shown, need_ellipsis

        reducible = [full - minimum for full, minimum in zip(full_lens, min_lens)]
        total_reducible = sum(reducible)
        if total_reducible <= 0:
            shown = shown[:-1]
            removed_from_end = True
            continue

        reductions = [0 for _ in shown]
        used = 0
        for index, reducible_chars in enumerate(reducible):
            if reducible_chars <= 0:
                continue
            cut = int(excess * (reducible_chars / total_reducible))
            cut = min(cut, reducible_chars)
            reductions[index] = cut
            used += cut

        remaining = excess - used
        if remaining > 0:
            order = sorted(
                range(len(shown)),
                key=lambda index: reducible[index],
                reverse=True,
            )
            for index in order:
                if remaining <= 0:
                    break
                capacity = reducible[index] - reductions[index]
                if capacity <= 0:
                    continue
                take = min(capacity, remaining)
                reductions[index] += take
                remaining -= take

        truncated: list[tuple[str, str]] = []
        for (key, value), full_len, minimum_len, reduction in zip(
            shown,
            full_lens,
            min_lens,
            reductions,
        ):
            target = max(full_len - reduction, minimum_len)
            truncated_value, _ = truncate_to_width(value, target)
            truncated.append((key, truncated_value))
        return truncated, need_ellipsis


def format_duration(duration: datetime.timedelta) -> str:
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


def render_status_icon(
    terminal: tui_terminal.Terminal,
    status: typing.Optional[vocode_state.ToolCallReqStatus],
    *,
    frame_index: int = 0,
    animate_running: bool = True,
) -> str:
    if status is None:
        return ""

    unicode_manager = terminal.unicode
    if status is vocode_state.ToolCallReqStatus.EXECUTING:
        if not animate_running:
            return unicode_manager.glyph("hourglass_with_flowing_sand")
        return unicode_manager.spinner_frame(
            frame_index,
            tui_unicode.SpinnerVariant.BRAILLE,
        )

    icon_name = _STATUS_ICON_NAME.get(status)
    if icon_name is None:
        return ""
    return unicode_manager.glyph(icon_name)


def format_status_text(
    status: typing.Optional[vocode_state.ToolCallReqStatus],
    duration: typing.Optional[datetime.timedelta] = None,
) -> str:
    if status is None:
        return ""
    if status is vocode_state.ToolCallReqStatus.REQUIRES_CONFIRMATION:
        return "waiting"
    if status is vocode_state.ToolCallReqStatus.PENDING_EXECUTION:
        return "pending"
    if status is vocode_state.ToolCallReqStatus.EXECUTING:
        return "running"
    if status is vocode_state.ToolCallReqStatus.REJECTED:
        return "rejected"
    if status is vocode_state.ToolCallReqStatus.COMPLETE:
        if duration is not None:
            return f"done, {format_duration(duration)}"
        return "done"
    return status.value.replace("_", " ")


def build_status_suffix(context: tui_tcf.ToolCallRenderContext) -> str:
    if not context.show_execution_stats:
        return ""
    return format_status_text(context.status, context.duration)


def build_tool_line(
    terminal: tui_terminal.Terminal,
    title: str,
    pairs: typing.Optional[list[tuple[str, str]]] = None,
    context: typing.Optional[tui_tcf.ToolCallRenderContext] = None,
    prefix_icon: typing.Optional[str] = None,
    output_text: typing.Optional[str] = None,
) -> rich_text.Text:
    text = rich_text.Text(no_wrap=True)
    icon = prefix_icon or ""
    if not icon:
        icon = terminal.unicode.glyph(":circle:")

    text.append(icon, style=tui_styles.TOOL_CALL_BULLET_STYLE)
    text.append(" ")
    text.append(title, style=tui_styles.TOOL_CALL_NAME_STYLE)

    if pairs:
        text.append(" ")
        for index, (key, value) in enumerate(pairs):
            if index > 0:
                text.append(", ", style=tui_styles.TOOL_CALL_META_STYLE)
            text.append(key, style=tui_styles.TOOL_CALL_KV_KEY_STYLE)
            text.append("=", style=tui_styles.TOOL_CALL_KV_EQ_STYLE)
            text.append(value, style=tui_styles.TOOL_CALL_KV_VALUE_STYLE)

    if output_text:
        text.append(" => ", style=tui_styles.TOOL_CALL_META_STYLE)
        text.append(output_text, style=tui_styles.TOOL_CALL_KV_VALUE_STYLE)

    suffix = ""
    if context is not None:
        suffix = build_status_suffix(context)
    if suffix:
        text.append(" ", style=tui_styles.TOOL_CALL_META_STYLE)
        text.append(f"[{suffix}]", style=tui_styles.TOOL_CALL_STATUS_STYLE)

    max_width = terminal.console.size.width
    if context is not None and context.max_width > 0:
        max_width = context.max_width
    if max_width > 0:
        text.truncate(max_width, overflow="ellipsis")
    return text
