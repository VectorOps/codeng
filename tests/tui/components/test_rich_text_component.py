from __future__ import annotations

import typing

from rich import console as rich_console

from vocode.tui.lib import base as tui_base
from vocode.tui.lib.components import (
    rich_text_component as components_rich_text_component,
)


class DummyTerminal:
    def __init__(self, console: rich_console.Console) -> None:
        self.console = console
        self.notified: list[tui_base.Component] = []

    def notify_component(self, component: tui_base.Component) -> None:
        self.notified.append(component)


def _render_text(
    component: tui_base.Component, console: rich_console.Console
) -> list[str]:
    component.terminal = DummyTerminal(console)
    lines = component.render(console.options)
    rendered_lines: list[str] = []
    for line in lines:
        parts: list[str] = []
        for segment in line:
            parts.append(segment.text)
        rendered_lines.append("".join(parts))
    return rendered_lines


def test_rich_text_component_renders_rich_markup() -> None:
    console = rich_console.Console(width=40, height=5, record=True)
    component = components_rich_text_component.RichTextComponent("[bold red]Hello[/]")
    rendered_lines = _render_text(component, console)
    assert rendered_lines
    assert "Hello" in rendered_lines[0]
    assert "[" not in rendered_lines[0]
    assert "]" not in rendered_lines[0]


def test_rich_text_component_marks_dirty_on_text_change() -> None:
    console = rich_console.Console(width=40, height=5, record=True)
    terminal = DummyTerminal(console)
    component = components_rich_text_component.RichTextComponent("initial")
    component.terminal = terminal
    assert terminal.notified == []
    component.text = "changed"
    assert terminal.notified == [component]
    component.text = "changed"
    assert terminal.notified == [component]


def test_rich_text_component_compacts_when_collapsed() -> None:
    console = rich_console.Console(width=40, height=50, record=True)
    text = "\n".join([f"line {i}" for i in range(20)])
    component = components_rich_text_component.RichTextComponent(text)
    component.set_collapsed(True)
    rendered_lines = _render_text(component, console)
    assert len(rendered_lines) == 11
    assert rendered_lines[-1].startswith("... (")


def test_rich_text_component_toggle_collapsed_invalidates_terminal() -> None:
    console = rich_console.Console(width=40, height=50, record=True)
    terminal = DummyTerminal(console)
    component = components_rich_text_component.RichTextComponent("hello")
    component.terminal = terminal
    assert terminal.notified == []
    component.toggle_collapsed()
    assert terminal.notified == [component]
