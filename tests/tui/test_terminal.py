from __future__ import annotations

import asyncio
import io

from rich import console as rich_console
from rich import segment as rich_segment

from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.lib.input import base as input_base
import pytest


class DummyComponent(tui_terminal.Component):
    def __init__(self, text: str, id: str | None = None) -> None:
        super().__init__(id=id)
        self.text = text
    def render(self) -> tui_terminal.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        return terminal.console.render_lines(self.text)


class MultiLineComponent(tui_terminal.Component):
    def __init__(self, lines: list[str], id: str | None = None) -> None:
        super().__init__(id=id)
        self.lines = lines
    def render(self) -> tui_terminal.Lines:
        return [[rich_segment.Segment(line)] for line in self.lines]


class InputComponent(tui_terminal.Component):
    def __init__(self, text: str, id: str | None = None) -> None:
        super().__init__(id=id)
        self.text = text
        self.key_events: list[input_base.KeyEvent] = []
        self.mouse_events: list[input_base.MouseEvent] = []

    def render(self) -> tui_terminal.Lines:
        return []

    def on_key_event(self, event: input_base.KeyEvent) -> None:
        self.key_events.append(event)

    def on_mouse_event(self, event: input_base.MouseEvent) -> None:
        self.mouse_events.append(event)


def test_terminal_renders_on_append() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    terminal = tui_terminal.Terminal(console=console)
    component = DummyComponent("hello")

    terminal.append_component(component)
    terminal.render()

    output = buffer.getvalue()
    assert tui_terminal.SYNC_UPDATE_START in output
    assert tui_terminal.ERASE_SCROLLBACK in output
    assert "hello" in output


def test_terminal_no_render_without_changes() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    terminal = tui_terminal.Terminal(console=console)
    component = DummyComponent("hello")

    terminal.append_component(component)
    terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    terminal.render()
    output = buffer.getvalue()
    assert output == ""


def test_terminal_incremental_render_updates_component() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    terminal = tui_terminal.Terminal(console=console)
    component = DummyComponent("first")

    terminal.append_component(component)
    terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    component.text = "second"
    terminal.notify_component(component)
    terminal.render()

    output = buffer.getvalue()
    assert "second" in output
    assert tui_terminal.ERASE_SCROLLBACK not in output


def test_incremental_render_updates_bottom_line_only_for_multiline_component() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        height=5,
    )
    terminal = tui_terminal.Terminal(console=console)
    component = DummyComponent("line1\nline2\nbottom1")

    terminal.append_component(component)
    terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    component.text = "line1\nline2\nbottom2"
    terminal.notify_component(component)
    terminal.render()

    output = buffer.getvalue()
    cursor_up_once = tui_terminal.CURSOR_PREVIOUS_LINE_FMT.format(1)
    assert "bottom2" in output
    assert "line1" not in output
    assert "line2" not in output
    assert "bottom1" not in output
    assert tui_terminal.ERASE_SCROLLBACK not in output
    assert cursor_up_once in output
    assert output.count(cursor_up_once) == 1


def test_incremental_render_appends_line_with_offscreen_top() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        height=2,
    )
    terminal = tui_terminal.Terminal(console=console)
    component = MultiLineComponent(["one", "two", "three"])

    terminal.append_component(component)
    terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    component.lines.append("four")
    terminal.notify_component(component)
    terminal.render()

    output = buffer.getvalue()
    assert "four" in output
    assert "one" not in output
    assert "two" not in output
    assert "three" not in output
    assert tui_terminal.ERASE_SCROLLBACK not in output


def test_insert_component_at_beginning() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    terminal = tui_terminal.Terminal(console=console)

    first = DummyComponent("first")
    second = DummyComponent("second")
    terminal.append_component(first)
    terminal.append_component(second)
    terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    zero = DummyComponent("zero")
    terminal.insert_component(0, zero)
    terminal.render()

    output = buffer.getvalue()
    assert "zero" in output
    assert "first" in output
    assert "second" in output
    assert output.index("zero") < output.index("first") < output.index("second")
    assert tui_terminal.ERASE_SCROLLBACK in output


def test_insert_component_negative_index_before_last() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    terminal = tui_terminal.Terminal(console=console)

    a = DummyComponent("a")
    b = DummyComponent("b")
    c = DummyComponent("c")
    terminal.append_component(a)
    terminal.append_component(b)
    terminal.append_component(c)
    terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    x = DummyComponent("x")
    terminal.insert_component(-1, x)
    terminal.render()

    output = buffer.getvalue()
    assert "a" in output
    assert "b" in output
    assert "x" in output
    assert "c" in output
    assert output.index("a") < output.index("b") < output.index("x") < output.index("c")


def test_insert_component_id_conflict_raises() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    terminal = tui_terminal.Terminal(console=console)

    first = DummyComponent("one", id="same")
    second = DummyComponent("two", id="same")
    terminal.append_component(first)
    try:
        terminal.insert_component(0, second)
        raise AssertionError("Expected ValueError for duplicate id")
    except ValueError:
        pass


@pytest.mark.asyncio
async def test_terminal_initializes_clearing_screen() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    terminal = tui_terminal.Terminal(console=console)
    await terminal.start()

    output = buffer.getvalue()
    assert tui_terminal.ERASE_SCREEN in output
    assert tui_terminal.CURSOR_HOME in output
    assert tui_terminal.ERASE_SCROLLBACK not in output


def test_focus_stack_routes_key_and_mouse_events_to_top_component() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    terminal = tui_terminal.Terminal(console=console)

    first = InputComponent("first", id="first")
    second = InputComponent("second", id="second")
    terminal.append_component(first)
    terminal.append_component(second)

    terminal.push_focus(first)
    key_event_1 = input_base.KeyEvent(action="down", key="a", text="a")
    mouse_event_1 = input_base.MouseEvent(action="move", x=1, y=1)
    terminal._handle_input_event(key_event_1)
    terminal._handle_input_event(mouse_event_1)

    assert first.key_events == [key_event_1]
    assert first.mouse_events == [mouse_event_1]
    assert second.key_events == []
    assert second.mouse_events == []

    terminal.push_focus(second)
    key_event_2 = input_base.KeyEvent(action="down", key="b", text="b")
    mouse_event_2 = input_base.MouseEvent(action="down", x=2, y=2, button="left")
    terminal._handle_input_event(key_event_2)
    terminal._handle_input_event(mouse_event_2)

    assert first.key_events == [key_event_1]
    assert first.mouse_events == [mouse_event_1]
    assert second.key_events == [key_event_2]
    assert second.mouse_events == [mouse_event_2]

    terminal.remove_focus(second)
    key_event_3 = input_base.KeyEvent(action="down", key="c", text="c")
    terminal._handle_input_event(key_event_3)
    assert first.key_events == [key_event_1, key_event_3]
    assert second.key_events == [key_event_2]


@pytest.mark.asyncio
async def test_terminal_start_and_stop_input_handler() -> None:
    class DummyInputHandler(input_base.InputHandler):
        def __init__(self) -> None:
            super().__init__()
            self.started = False
            self.cancelled = False

        async def run(self) -> None:
            self.started = True
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    handler = DummyInputHandler()
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    terminal = tui_terminal.Terminal(console=console, input_handler=handler)

    await terminal.start()
    assert handler.started

    await terminal.stop()
    assert handler.cancelled
