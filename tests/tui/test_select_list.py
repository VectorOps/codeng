from __future__ import annotations

import io

import pytest
from rich import console as rich_console
from rich import style as rich_style

from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.lib.components import select_list as tui_select_list
from vocode.tui.lib.input import base as input_base


def test_select_list_renders_max_five_and_hint() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        width=40,
    )
    terminal = tui_terminal.Terminal(console=console)
    items = [
        tui_select_list.SelectItem(id=f"id-{i}", text=f"Item {i}") for i in range(10)
    ]
    component = tui_select_list.SelectListComponent(items=items, id="select")
    terminal.append_component(component)
    options = console.options
    lines = component.render(options)
    assert len(lines) == 6
    combined_lines = ["".join(segment.text for segment in line) for line in lines]
    assert "Item 0" in combined_lines[0]
    assert "Item 4" in combined_lines[4]
    all_text = "".join(combined_lines)
    assert "Item 5" not in all_text
    assert "Showing 5 of 10 items" in combined_lines[-1]


def test_select_list_highlights_selected_item() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        width=40,
    )
    terminal = tui_terminal.Terminal(console=console)
    items = [
        tui_select_list.SelectItem(id="a", text="First item"),
        tui_select_list.SelectItem(id="b", text="Second item"),
    ]
    component = tui_select_list.SelectListComponent(items=items, id="select")
    terminal.append_component(component)
    options = console.options
    lines = component.render(options)
    assert lines
    selected_index = component.selected_index
    assert selected_index == 0
    selected_line = lines[selected_index]
    has_reverse = False
    for segment in selected_line:
        style = segment.style
        if isinstance(style, rich_style.Style) and style.reverse:
            has_reverse = True
            break
    assert has_reverse
    down_event = input_base.KeyEvent(action="down", key="down")
    component.on_key_event(down_event)
    assert component.selected_index == 1
    lines = component.render(options)
    selected_line = lines[component.selected_index]
    has_reverse = any(
        isinstance(segment.style, rich_style.Style) and segment.style.reverse
        for segment in selected_line
    )
    assert has_reverse


def test_select_list_on_select_notifies_subscribers() -> None:
    items = [
        tui_select_list.SelectItem(id="id-0", text="Item 0"),
        tui_select_list.SelectItem(id="id-1", text="Item 1"),
        tui_select_list.SelectItem(id="id-2", text="Item 2"),
    ]
    component = tui_select_list.SelectListComponent(items=items)
    selected: list[tui_select_list.SelectItem] = []

    def subscriber(item: tui_select_list.SelectItem) -> None:
        selected.append(item)

    component.subscribe_select(subscriber)
    down_event = input_base.KeyEvent(action="down", key="down")
    component.on_key_event(down_event)
    component.on_key_event(down_event)
    enter_event = input_base.KeyEvent(action="down", key="enter")
    component.on_key_event(enter_event)
    assert len(selected) == 1
    assert selected[0].id == "id-2"


def test_select_list_enter_on_empty_does_not_notify_subscribers() -> None:
    component = tui_select_list.SelectListComponent()
    selected: list[tui_select_list.SelectItem] = []

    def subscriber(item: tui_select_list.SelectItem) -> None:
        selected.append(item)

    component.subscribe_select(subscriber)
    enter_event = input_base.KeyEvent(action="down", key="enter")
    component.on_key_event(enter_event)
    assert selected == []


def test_select_list_item_management_and_selection_bounds() -> None:
    component = tui_select_list.SelectListComponent()
    assert component.items == []
    assert component.selected_index is None
    component.add_item({"id": "a", "text": "Item A"})
    component.add_item(tui_select_list.SelectItem(id="b", text="Item B"))
    assert [item.id for item in component.items] == ["a", "b"]
    assert component.selected_item is not None
    assert component.selected_item.id == "a"
    component.set_items(
        [
            {"id": "x", "text": "Item X"},
            {"id": "y", "text": "Item Y"},
        ]
    )
    assert [item.id for item in component.items] == ["x", "y"]
    assert component.selected_item is not None
    assert component.selected_item.id == "x"
    component.remove_item_by_id("x")
    assert [item.id for item in component.items] == ["y"]
    assert component.selected_item is not None
    assert component.selected_item.id == "y"
    component.remove_item_by_id("y")
    assert component.items == []
    assert component.selected_index is None
    assert component.selected_item is None
