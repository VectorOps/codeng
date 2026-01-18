from __future__ import annotations

from rich import console as rich_console

from vocode.tui.lib import base as tui_base
from vocode.tui.lib.components import (
    markdown_component as components_markdown_component,
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
        rendered_lines.append("".join(segment.text for segment in line))
    return rendered_lines


def test_markdown_component_compacts_when_collapsed() -> None:
    console = rich_console.Console(width=60, height=200, record=True)
    markdown = "\n".join([f"- item {i}" for i in range(20)])
    component = components_markdown_component.MarkdownComponent(markdown)
    component.set_collapsed(True)
    rendered_lines = _render_text(component, console)
    assert len(rendered_lines) == 11
    assert rendered_lines[-1].startswith("... (")
