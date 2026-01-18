from __future__ import annotations

import typing

from rich import console as rich_console
from rich import table as rich_table
from rich import text as rich_text

from vocode.tui import command_manager as tui_command_manager
from vocode.tui import lib as tui_terminal
from vocode.tui.lib.components import renderable as renderable_component


class CommandManagerHelpComponent(renderable_component.RenderableComponentBase):
    def __init__(
        self,
        hotkeys: list[tui_command_manager.Hotkey],
        id: str | None = None,
        component_style: tui_terminal.ComponentStyle | None = None,
    ) -> None:
        super().__init__(
            id=id,
            component_style=component_style,
        )
        self._hotkeys = list(hotkeys)

    @property
    def hotkeys(self) -> list[tui_command_manager.Hotkey]:
        return list(self._hotkeys)

    def _build_renderable(
        self,
        console: rich_console.Console,
    ) -> tui_terminal.Renderable:
        by_category: dict[str, list[tui_command_manager.Hotkey]] = {}
        for hotkey in self._hotkeys:
            by_category.setdefault(hotkey.category, []).append(hotkey)

        categories = sorted(by_category.keys())

        header = rich_text.Text()
        header.append("Command manager", style="bold")
        header.append("  (C+X or ESC to close)\n", style="dim")

        table = rich_table.Table.grid(padding=(0, 4))
        for _ in range(8):
            table.add_column()

        def build_column(category: str) -> tui_terminal.Renderable:
            lines: list[tui_terminal.Renderable] = []

            header_line = rich_text.Text()
            header_line.append(category, style="bold")
            lines.append(header_line)

            items = sorted(by_category[category], key=lambda h: h.name.lower())
            for item in items:
                key_text = tui_command_manager.format_keybinding(item.mapping)
                line = rich_text.Text()
                line.append(key_text, style="green")
                line.append(" ")
                line.append(item.name, style="white")
                lines.append(line)

            return rich_console.Group(*lines)

        for index in range(0, len(categories), 8):
            row_categories = categories[index : index + 8]
            cells: list[typing.Any] = [
                build_column(category) for category in row_categories
            ]
            while len(cells) < 8:
                cells.append("")
            table.add_row(*cells)

        if not categories:
            return header

        return rich_console.Group(header, table)
