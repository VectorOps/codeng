from __future__ import annotations

import typing

from rich import console as rich_console
from rich import text as rich_text

from vocode.tui.lib import base as tui_base
from vocode.tui.lib.components import (
    callback_renderable_component as components_callback_renderable_component,
)


class DummyTerminal:
    def __init__(self, console: rich_console.Console) -> None:
        self.console = console
        self.notified: list[tui_base.Component] = []

    def notify_component(self, component: tui_base.Component) -> None:
        self.notified.append(component)


def _render_text(
    component: tui_base.Component,
    console: rich_console.Console,
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


def test_callback_renderable_component_renders_rich_renderable() -> None:
    console = rich_console.Console(width=40, height=5, record=True)

    def render_fn(
        console_arg: rich_console.Console,
    ) -> rich_text.Text:
        return rich_text.Text.from_markup("[bold green]Hello[/]")

    component = components_callback_renderable_component.CallbackRenderableComponent(
        render_fn,
    )
    rendered_lines = _render_text(component, console)
    assert rendered_lines
    assert "Hello" in rendered_lines[0]
    assert "[" not in rendered_lines[0]
    assert "]" not in rendered_lines[0]


def test_callback_renderable_component_marks_dirty_on_render_fn_change() -> None:
    console = rich_console.Console(width=40, height=5, record=True)
    terminal = DummyTerminal(console)

    def render_one(
        console_arg: rich_console.Console,
    ) -> rich_text.Text:
        return rich_text.Text("one")

    def render_two(
        console_arg: rich_console.Console,
    ) -> rich_text.Text:
        return rich_text.Text("two")

    component = components_callback_renderable_component.CallbackRenderableComponent(
        render_one,
    )
    component.terminal = terminal
    assert terminal.notified == []
    component.render_fn = render_two
    assert terminal.notified == [component]
    component.render_fn = render_two
    assert terminal.notified == [component]
