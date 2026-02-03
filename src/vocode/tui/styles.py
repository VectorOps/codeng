from __future__ import annotations

from rich import box as rich_box

from vocode.tui import lib as tui_terminal

INPUT_MESSAGE_COMPONENT_STYLE = tui_terminal.ComponentStyle(
    padding_pad=1,
    padding_style="on rgb(60,60,60)",
    margin_bottom=1,
)

INPUT_COMPONENT_STYLE = tui_terminal.ComponentStyle(
    padding_pad=1,
    padding_style="on rgb(60,60,60)",
)
INPUT_PANEL_COMPONENT_STYLE = tui_terminal.ComponentStyle(
    panel_box=rich_box.ROUNDED,
    panel_style="on rgb(60,60,60)",
    panel_title_align="left",
)

OUTPUT_MESSAGE_STYLE = tui_terminal.ComponentStyle(
    margin_bottom=1,
)

TOOLBAR_COMPONENT_STYLE = tui_terminal.ComponentStyle()

TOOL_CALL_DURATION_STYLE = "dim grey50"
TOOL_CALL_NAME_STYLE = "bright_cyan"
TOOL_CALL_BULLET_STYLE = "bold yellow"
TOOL_CALL_META_STYLE = "dim grey50"
TOOL_CALL_KV_KEY_STYLE = "bright_yellow"
TOOL_CALL_KV_EQ_STYLE = "dim grey50"
TOOL_CALL_KV_VALUE_STYLE = "bright_green"

TOOL_CALL_OUTPUT_BLOCK_STYLE = "on rgb(0,80,0)"
