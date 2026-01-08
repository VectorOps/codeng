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

TOOLBAR_COMPONENT_STYLE = tui_terminal.ComponentStyle(
    padding_pad=1,
)
