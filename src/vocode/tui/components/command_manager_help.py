from __future__ import annotations

import typing

from rich import console as rich_console
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

        body = rich_text.Text()
        body.append("Command manager", style="bold")
        body.append("  (Ctrl+X or ESC to close)\n", style="dim")

        for category in categories:
            body.append("\n")
            body.append(f"{category}\n", style="bold")
            items = sorted(by_category[category], key=lambda h: h.name.lower())
            for item in items:
                key_text = tui_command_manager.format_keybinding(item.mapping)
                body.append(f"  {key_text:<12} {item.name}\n")

        return body
