from __future__ import annotations

import typing

from rich import console as rich_console

from vocode.tui.lib import base as tui_base
from vocode.tui.lib.components import (
    composite_component as components_composite_component,
)
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
        rendered_lines.append("".join(segment.text for segment in line))
    return rendered_lines


class DummyChild(tui_base.Component):
    def __init__(self, id: str | None = None) -> None:
        super().__init__(id=id)
        self._collapsed = False
        self.key_events: list[typing.Any] = []
        self.mouse_events: list[typing.Any] = []

    def render(self, options: rich_console.ConsoleOptions) -> tui_base.Lines:
        return []

    def on_key_event(self, event: typing.Any) -> None:
        self.key_events.append(event)

    def on_mouse_event(self, event: typing.Any) -> None:
        self.mouse_events.append(event)


def test_child_mark_dirty_notifies_terminal_via_parent() -> None:
    console = rich_console.Console(width=40, height=5, record=True)
    terminal = DummyTerminal(console)
    composite = components_composite_component.CompositeComponent()
    child = components_rich_text_component.RichTextComponent("initial")
    composite.terminal = terminal
    composite.add_child(child)
    assert terminal.notified == [composite]
    terminal.notified.clear()
    child.text = "changed"
    assert terminal.notified == [composite]


def test_composite_focus_routes_events_and_collapse_to_child() -> None:
    composite = components_composite_component.CompositeComponent()
    child1 = DummyChild()
    child2 = DummyChild()
    composite.add_child(child1)
    composite.add_child(child2)
    composite.focus_child(child2)
    composite.on_key_event("key-event")
    composite.on_mouse_event("mouse-event")
    assert child1.key_events == []
    assert child1.mouse_events == []
    assert child2.key_events == ["key-event"]
    assert child2.mouse_events == ["mouse-event"]
    assert child2.supports_collapse
    assert not child2.is_collapsed
    composite.set_collapsed(True)
    assert child2.is_collapsed
    composite.toggle_collapsed()
    assert child2.is_expanded


def test_composite_render_renders_all_visible_children() -> None:
    console = rich_console.Console(width=40, height=5, record=True)
    terminal = DummyTerminal(console)
    composite = components_composite_component.CompositeComponent()
    child1 = components_rich_text_component.RichTextComponent("first")
    child2 = components_rich_text_component.RichTextComponent("second")
    composite.terminal = terminal
    composite.add_child(child1)
    composite.add_child(child2)
    lines = composite.render(console.options)
    rendered_lines: list[str] = []
    for line in lines:
        rendered_lines.append("".join(segment.text for segment in line))
    assert rendered_lines == ["first", "second"]
    child1.is_hidden = True
    lines_hidden = composite.render(console.options)
    rendered_hidden: list[str] = []
    for line in lines_hidden:
        rendered_hidden.append("".join(segment.text for segment in line))
    assert rendered_hidden == ["second"]
