from __future__ import annotations

import typing

from rich.console import Console

from vocode.tui import command_manager as tui_command_manager
from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.screens import base_viewer


class KeybindingsViewScreen(base_viewer.TextViewerScreen):
    def __init__(
        self,
        terminal: tui_terminal.Terminal,
        sections: list[tuple[str, list[tuple[str, str]]]],
    ) -> None:
        text = self._build_text(terminal.console, sections)
        super().__init__(terminal=terminal, text=text, initial_bottom=False)

    @classmethod
    def _build_text(
        cls,
        console: Console,
        sections: list[tuple[str, list[tuple[str, str]]]],
    ) -> str:
        width = console.size.width
        if width <= 0:
            width = 80
        column_width = max(min((width - 4) // 2, 44), 24)
        lines = ["Key bindings", ""]
        for title, items in sections:
            lines.append(title)
            lines.append("")
            lines.extend(cls._render_columns(items, column_width=column_width))
            lines.append("")
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    @staticmethod
    def _render_columns(
        items: list[tuple[str, str]],
        *,
        column_width: int,
    ) -> list[str]:
        if not items:
            return ["  (none)"]
        rendered = [
            KeybindingsViewScreen._format_item(
                key,
                description,
                column_width=column_width,
            )
            for key, description in items
        ]
        midpoint = (len(rendered) + 1) // 2
        left = rendered[:midpoint]
        right = rendered[midpoint:]
        lines: list[str] = []
        for index in range(max(len(left), len(right))):
            left_text = left[index] if index < len(left) else ""
            right_text = right[index] if index < len(right) else ""
            if right_text:
                lines.append(f"  {left_text:<{column_width}}  {right_text}")
            else:
                lines.append(f"  {left_text}")
        return lines

    @staticmethod
    def _format_item(key: str, description: str, *, column_width: int) -> str:
        key_text = f"{key:<10}"
        description_width = max(column_width - 11, 8)
        if len(description) > description_width:
            description = description[: description_width - 3] + "..."
        return f"{key_text} {description}"


def build_keybinding_sections(
    hotkeys: list[tui_command_manager.Hotkey],
) -> list[tuple[str, list[tuple[str, str]]]]:
    command_items = [
        (tui_command_manager.format_keybinding(hotkey.mapping), hotkey.name)
        for hotkey in hotkeys
    ]
    input_items = [
        ("Enter", "Submit input"),
        ("Alt+Enter", "Insert newline"),
        ("Ctrl+A", "Move to line start"),
        ("Ctrl+E", "Move to line end"),
        ("Ctrl+B", "Move left"),
        ("Ctrl+F", "Move right"),
        ("Ctrl+P", "Move up"),
        ("Ctrl+N", "Move down"),
        ("Alt+B", "Move to previous word"),
        ("Alt+F", "Move to next word"),
        ("Ctrl+K", "Delete to line end"),
        ("Ctrl+U", "Delete to line start"),
        ("Ctrl+W", "Delete previous word"),
        ("Alt+D", "Delete next word"),
        ("Ctrl+L", "Clear input buffer"),
        ("Up/Down", "History previous or next"),
        ("Ctrl+R", "Open history search"),
    ]
    global_items = [
        ("Ctrl+Space", "Open command manager"),
        ("Ctrl+C", "Stop current workflow"),
        ("Ctrl+D", "Arm or confirm exit"),
        ("Esc", "Close command manager or viewer"),
    ]
    return [
        ("Global", global_items),
        ("Input editor", input_items),
        ("Command manager", command_items),
    ]
