from __future__ import annotations

import asyncio
import io

from rich import console as rich_console
from rich import panel as rich_panel
from rich import padding as rich_padding
from rich import segment as rich_segment
from rich import style as rich_style

from vocode.tui import lib as tui_terminal
from vocode.tui import uistate as tui_uistate
from vocode.tui.lib import controls as tui_controls
from vocode.tui.lib.components import input_component as tui_input_component
from vocode.tui.lib.input import base as input_base
import pytest


class DummyComponent(tui_terminal.Component):
    def __init__(self, text: str, id: str | None = None) -> None:
        super().__init__(id=id)
        self.text = text

    def render(
        self,
        options: rich_console.ConsoleOptions,
    ) -> tui_terminal.Lines:
        terminal = self.terminal
        if terminal is None:
            return []
        return terminal.console.render_lines(self.text, options=options)


class MultiLineComponent(tui_terminal.Component):
    def __init__(self, lines: list[str], id: str | None = None) -> None:
        super().__init__(id=id)
        self.lines = lines

    def render(
        self,
        options: rich_console.ConsoleOptions,
    ) -> tui_terminal.Lines:
        return [[rich_segment.Segment(line)] for line in self.lines]


class InputComponent(tui_terminal.Component):
    def __init__(self, text: str, id: str | None = None) -> None:
        super().__init__(id=id)
        self.text = text
        self.key_events: list[input_base.KeyEvent] = []
        self.mouse_events: list[input_base.MouseEvent] = []

    def render(
        self,
        options: rich_console.ConsoleOptions,
    ) -> tui_terminal.Lines:
        return []

    def on_key_event(self, event: input_base.KeyEvent) -> None:
        self.key_events.append(event)

    def on_mouse_event(self, event: input_base.MouseEvent) -> None:
        self.mouse_events.append(event)


@pytest.mark.asyncio
async def test_terminal_renders_on_append() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)
    component = DummyComponent("hello")

    terminal.append_component(component)
    await terminal.render()

    output = buffer.getvalue()
    assert tui_controls.SYNC_UPDATE_START in output
    assert tui_controls.ERASE_SCROLLBACK in output
    assert "hello" in output


@pytest.mark.asyncio
async def test_hidden_component_skipped_in_render_and_cache() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)

    component = DummyComponent("hello")
    terminal.append_component(component)
    await terminal.render()

    first_output = buffer.getvalue()
    assert "hello" in first_output

    buffer.truncate(0)
    buffer.seek(0)

    component.is_hidden = True
    await terminal.render()

    hidden_output = buffer.getvalue()
    assert "hello" not in hidden_output
    cache_lines = terminal._cache.get(component)
    assert cache_lines == []

    buffer.truncate(0)
    buffer.seek(0)

    component.is_hidden = False
    await terminal.render()

    shown_output = buffer.getvalue()
    assert "hello" in shown_output
    cache_lines_after = terminal._cache.get(component)
    assert cache_lines_after is not None
    assert len(cache_lines_after) > 0


@pytest.mark.asyncio
async def test_auto_render_not_scheduled_before_start() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=True, min_render_interval_ms=0)
    terminal = tui_terminal.Terminal(console=console, settings=settings)
    component = DummyComponent("hello")

    terminal.append_component(component)

    assert terminal._auto_render_task is None


@pytest.mark.asyncio
async def test_auto_render_scheduled_after_start() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=True, min_render_interval_ms=0)
    terminal = tui_terminal.Terminal(console=console, settings=settings)
    component = DummyComponent("hello")

    terminal.append_component(component)
    await terminal.start()
    await asyncio.sleep(0)

    output = buffer.getvalue()
    assert "hello" in output


def test_input_component_handles_keys_and_renders_cursor() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        width=20,
    )
    terminal = tui_terminal.Terminal(console=console)
    component = tui_input_component.InputComponent("", id="input")
    terminal.append_component(component)

    key_event_a = input_base.KeyEvent(action="down", key="char", text="a")
    component.on_key_event(key_event_a)

    key_event_left = input_base.KeyEvent(action="down", key="left")
    component.on_key_event(key_event_left)

    key_event_b = input_base.KeyEvent(action="down", key="char", text="b")
    component.on_key_event(key_event_b)

    assert component.text == "ba"

    options = console.options
    lines = component.render(options)
    assert lines

    first_line = lines[0]
    combined_text = "".join(segment.text for segment in first_line)
    assert "ba" in combined_text

    highlighted_index = component.cursor_col
    assert 0 <= highlighted_index < len(combined_text)

    current_index = 0
    cursor_style: rich_style.Style | None = None
    for segment in first_line:
        text = segment.text
        length = len(text)
        next_index = current_index + length
        if current_index <= highlighted_index < next_index:
            style = segment.style
            if isinstance(style, rich_style.Style):
                cursor_style = style
            break
        current_index = next_index

    assert cursor_style is not None
    assert cursor_style.reverse


def test_input_component_submit_notifies_subscribers() -> None:
    component = tui_input_component.InputComponent("")
    submitted: list[str] = []

    def subscriber(value: str) -> None:
        submitted.append(value)

    component.subscribe_submit(subscriber)

    key_event_h = input_base.KeyEvent(action="down", key="char", text="h")
    key_event_i = input_base.KeyEvent(action="down", key="char", text="i")
    component.on_key_event(key_event_h)
    component.on_key_event(key_event_i)

    assert component.text == "hi"
    assert submitted == []

    submit_event = input_base.KeyEvent(
        action="down",
        key="enter",
        alt=True,
    )
    component.on_key_event(submit_event)

    assert submitted == ["hi"]

    plain_enter = input_base.KeyEvent(action="down", key="enter")
    component.on_key_event(plain_enter)
    assert component.text == "hi\n"


def test_input_component_single_line_enter_submits_without_newline() -> None:
    component = tui_input_component.InputComponent("", single_line=True)
    submitted: list[str] = []

    def subscriber(value: str) -> None:
        submitted.append(value)

    component.subscribe_submit(subscriber)

    key_event_h = input_base.KeyEvent(action="down", key="char", text="h")
    key_event_i = input_base.KeyEvent(action="down", key="char", text="i")
    component.on_key_event(key_event_h)
    component.on_key_event(key_event_i)

    assert component.text == "hi"
    assert submitted == []

    plain_enter = input_base.KeyEvent(
        action="down",
        key="enter",
        text="\n",
    )
    component.on_key_event(plain_enter)

    assert submitted == ["hi"]
    assert component.text == "hi"


def test_input_component_home_end_and_word_navigation() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        width=20,
    )
    terminal = tui_terminal.Terminal(console=console)
    component = tui_input_component.InputComponent("hello world", id="input")
    terminal.append_component(component)
    assert component.cursor_row == 0
    assert component.cursor_col == len("hello world")
    home_event = input_base.KeyEvent(action="down", key="home")
    component.on_key_event(home_event)
    assert component.cursor_col == 0
    alt_right = input_base.KeyEvent(action="down", key="right", alt=True)
    component.on_key_event(alt_right)
    assert component.cursor_col == len("hello ")
    end_event = input_base.KeyEvent(action="down", key="end")
    component.on_key_event(end_event)
    assert component.cursor_col == len("hello world")
    alt_left = input_base.KeyEvent(action="down", key="left", alt=True)
    component.on_key_event(alt_left)
    assert component.cursor_col == len("hello ")


def test_input_component_emacs_movement_keys() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        width=20,
    )
    terminal = tui_terminal.Terminal(console=console)
    component = tui_input_component.InputComponent("abc\ndef", id="input")
    terminal.append_component(component)

    assert component.cursor_row == 1
    assert component.cursor_col == len("def")

    ctrl_a = input_base.KeyEvent(action="down", key="a", ctrl=True)
    component.on_key_event(ctrl_a)
    assert component.cursor_row == 1
    assert component.cursor_col == 0

    ctrl_e = input_base.KeyEvent(action="down", key="e", ctrl=True)
    component.on_key_event(ctrl_e)
    assert component.cursor_row == 1
    assert component.cursor_col == len("def")

    ctrl_b = input_base.KeyEvent(action="down", key="b", ctrl=True)
    component.on_key_event(ctrl_b)
    assert component.cursor_row == 1
    assert component.cursor_col == len("def") - 1

    ctrl_f = input_base.KeyEvent(action="down", key="f", ctrl=True)
    component.on_key_event(ctrl_f)
    assert component.cursor_row == 1
    assert component.cursor_col == len("def")

    ctrl_p = input_base.KeyEvent(action="down", key="p", ctrl=True)
    component.on_key_event(ctrl_p)
    assert component.cursor_row == 0

    ctrl_n = input_base.KeyEvent(action="down", key="n", ctrl=True)
    component.on_key_event(ctrl_n)
    assert component.cursor_row == 1

    alt_b = input_base.KeyEvent(action="down", key="b", alt=True, text="b")
    component.text = "hello world"
    component.on_key_event(alt_b)
    assert component.cursor_row == 0
    assert component.cursor_col == len("hello ")

    alt_f = input_base.KeyEvent(action="down", key="f", alt=True, text="f")
    component.on_key_event(alt_f)
    assert component.cursor_row == 0
    assert component.cursor_col == len("hello world")


def test_input_component_emits_cursor_event_with_new_position() -> None:
    component = tui_input_component.InputComponent("ab")
    positions: list[tuple[int, int]] = []

    def on_cursor_event(row: int, col: int) -> None:
        positions.append((row, col))

    component.subscribe_cursor_event(on_cursor_event)

    left_event = input_base.KeyEvent(action="down", key="left")
    component.on_key_event(left_event)
    component.on_key_event(left_event)
    component.on_key_event(left_event)

    assert positions == [(0, 1), (0, 0)]


def test_input_component_kill_and_case_keybindings() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        width=40,
    )
    terminal = tui_terminal.Terminal(console=console)
    component = tui_input_component.InputComponent("hello world", id="input")
    terminal.append_component(component)

    home_event = input_base.KeyEvent(action="down", key="home")
    component.on_key_event(home_event)

    alt_right = input_base.KeyEvent(action="down", key="right", alt=True)
    component.on_key_event(alt_right)
    assert component.cursor_col == len("hello ")

    ctrl_k = input_base.KeyEvent(action="down", key="k", ctrl=True)
    component.on_key_event(ctrl_k)
    assert component.text == "hello "
    assert component.cursor_col == len("hello ")

    component.on_key_event(home_event)
    assert component.cursor_col == 0
    component.on_key_event(ctrl_k)
    assert component.text == ""
    assert component.cursor_col == 0

    component.text = "hello world"
    end_event = input_base.KeyEvent(action="down", key="end")
    component.on_key_event(end_event)
    ctrl_u = input_base.KeyEvent(action="down", key="u", ctrl=True)
    component.on_key_event(ctrl_u)
    assert component.text == ""
    assert component.cursor_col == 0

    component.text = "hello world"
    component.on_key_event(end_event)
    ctrl_w = input_base.KeyEvent(action="down", key="w", ctrl=True)
    component.on_key_event(ctrl_w)
    assert component.text == "hello "

    component.text = "hello world"
    component.on_key_event(home_event)
    alt_d = input_base.KeyEvent(action="down", key="d", alt=True, text="d")
    component.on_key_event(alt_d)
    assert component.text == "world"

    component.text = "hello world"
    component.on_key_event(home_event)
    alt_u = input_base.KeyEvent(action="down", key="u", alt=True, text="u")
    component.on_key_event(alt_u)
    assert component.text == "HELLO world"

    component.text = "HELLO WORLD"
    component.on_key_event(home_event)
    alt_l = input_base.KeyEvent(action="down", key="l", alt=True, text="l")
    component.on_key_event(alt_l)
    assert component.text == "hello WORLD"

    component.text = "hello world"
    component.on_key_event(home_event)
    alt_c = input_base.KeyEvent(action="down", key="c", alt=True, text="c")
    component.on_key_event(alt_c)
    assert component.text == "Hello world"


def test_input_component_multiline_scroll_and_height_cap() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        width=20,
        height=9,
    )
    terminal = tui_terminal.Terminal(console=console)
    lines = "\n".join(str(i) for i in range(10))
    component = tui_input_component.InputComponent(lines, id="input")
    terminal.append_component(component)

    component.set_cursor_position(0, 0)

    options = console.options
    rendered = component.render(options)
    assert len(rendered) == 6

    initial_top = component.scroll_top
    assert initial_top == 0

    for _ in range(9):
        down = input_base.KeyEvent(action="down", key="down")
        component.on_key_event(down)

    assert component.cursor_row == 9
    assert component.scroll_top > 0


@pytest.mark.asyncio
async def test_tui_state_input_history_navigation() -> None:
    async def on_input(value: str) -> None:
        return

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return

    handler = DummyInputHandler()
    state = tui_uistate.TUIState(
        on_input=on_input,
        input_handler=handler,
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )
    component = state._input_component

    state._handle_submit("first")
    state._handle_submit("second")

    assert component.text == ""

    key_up = input_base.KeyEvent(action="down", key="up")
    handler.publish(key_up)
    assert component.text == "second"

    handler.publish(key_up)
    assert component.text == "first"

    handler.publish(key_up)
    assert component.text == "first"

    key_down = input_base.KeyEvent(action="down", key="down")
    handler.publish(key_down)
    assert component.text == "second"

    handler.publish(key_down)
    assert component.text == ""


@pytest.mark.asyncio
async def test_terminal_no_render_without_changes() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)
    component = DummyComponent("hello")

    terminal.append_component(component)
    await terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    await terminal.render()
    output = buffer.getvalue()
    assert output == ""


@pytest.mark.asyncio
async def test_terminal_dirty_component_same_output_no_render() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
    )
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)
    component = DummyComponent("hello")

    terminal.append_component(component)
    await terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    terminal.notify_component(component)
    await terminal.render()

    output = buffer.getvalue()
    assert output == ""


@pytest.mark.asyncio
async def test_terminal_incremental_render_updates_component() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)
    component = DummyComponent("first")

    terminal.append_component(component)
    await terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    component.text = "second"
    terminal.notify_component(component)
    await terminal.render()

    output = buffer.getvalue()
    assert "second" in output
    assert tui_controls.ERASE_SCROLLBACK not in output
    assert tui_controls.ERASE_DOWN in output


@pytest.mark.asyncio
async def test_incremental_render_clear_to_bottom_mode_uses_erase_down() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
    )
    settings = tui_terminal.TerminalSettings(
        auto_render=False,
        incremental_mode=tui_terminal.IncrementalRenderMode.CLEAR_TO_BOTTOM,
    )
    terminal = tui_terminal.Terminal(console=console, settings=settings)
    component = DummyComponent("first")

    terminal.append_component(component)
    await terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    component.text = "second"
    terminal.notify_component(component)
    await terminal.render()

    output = buffer.getvalue()
    assert "second" in output
    assert tui_controls.ERASE_DOWN in output


@pytest.mark.asyncio
async def test_incremental_render_updates_bottom_line_only_for_multiline_component() -> (
    None
):
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        height=5,
    )
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)
    component = DummyComponent("line1\nline2\nbottom1")

    terminal.append_component(component)
    await terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    component.text = "line1\nline2\nbottom2"
    terminal.notify_component(component)
    await terminal.render()

    output = buffer.getvalue()
    cursor_up_once = tui_controls.CURSOR_PREVIOUS_LINE_FMT.format(1)
    assert "bottom2" in output
    assert "line1" not in output
    assert "line2" not in output
    assert "bottom1" not in output
    assert tui_controls.ERASE_SCROLLBACK not in output
    assert cursor_up_once in output
    assert output.count(cursor_up_once) == 1


@pytest.mark.asyncio
async def test_incremental_render_appends_line_with_offscreen_top() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        height=2,
    )
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)
    component = MultiLineComponent(["one", "two", "three"])

    terminal.append_component(component)
    await terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    component.lines.append("four")
    terminal.notify_component(component)
    await terminal.render()

    output = buffer.getvalue()
    assert "four" in output
    assert "one" not in output
    assert "two" not in output
    assert "three" not in output
    assert tui_controls.ERASE_SCROLLBACK not in output


@pytest.mark.asyncio
async def test_insert_component_at_beginning() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)

    first = DummyComponent("first")
    second = DummyComponent("second")
    terminal.append_component(first)
    terminal.append_component(second)
    await terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    zero = DummyComponent("zero")
    terminal.insert_component(0, zero)
    await terminal.render()

    output = buffer.getvalue()
    assert "zero" in output
    assert "first" in output
    assert "second" in output
    assert output.index("zero") < output.index("first") < output.index("second")
    # Previously asserted ERASE_SCROLLBACK here, but incremental insert
    # no longer forces a full re-render.


@pytest.mark.asyncio
async def test_insert_component_negative_index_before_last() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)

    a = DummyComponent("a")
    b = DummyComponent("b")
    c = DummyComponent("c")
    terminal.append_component(a)
    terminal.append_component(b)
    terminal.append_component(c)
    await terminal.render()

    buffer.truncate(0)
    buffer.seek(0)

    x = DummyComponent("x")
    terminal.insert_component(-1, x)
    await terminal.render()

    output = buffer.getvalue()
    assert "x" in output
    assert "c" in output
    assert output.index("x") < output.index("c")

    order = [component.text for component in terminal.components]
    assert order == ["a", "b", "x", "c"]

def test_insert_component_index_skips_removed_components() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)

    a = DummyComponent("a")
    b = DummyComponent("b")
    c = DummyComponent("c")
    terminal.append_component(a)
    terminal.append_component(b)
    terminal.append_component(c)

    terminal.remove_component(b)

    x = DummyComponent("x")
    terminal.insert_component(1, x)
    terminal._delete_removed_components()

    order = [component.text for component in terminal.components]
    assert order == ["a", "x", "c"]


def test_insert_component_negative_index_skips_removed_components() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)

    a = DummyComponent("a")
    b = DummyComponent("b")
    c = DummyComponent("c")
    terminal.append_component(a)
    terminal.append_component(b)
    terminal.append_component(c)

    terminal.remove_component(b)

    x = DummyComponent("x")
    terminal.insert_component(-1, x)
    terminal._delete_removed_components()

    order = [component.text for component in terminal.components]
    assert order == ["a", "x", "c"]


def test_insert_component_id_conflict_raises() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)

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
    assert tui_controls.ERASE_SCREEN in output
    assert tui_controls.CURSOR_HOME in output
    assert tui_controls.ERASE_SCROLLBACK not in output


def test_focus_stack_routes_key_and_mouse_events_to_top_component() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)

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


@pytest.mark.asyncio
async def test_terminal_full_render_triggered_by_resize_event() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)
    component = DummyComponent("hello")

    terminal.append_component(component)
    await terminal.render()

    first_output = buffer.getvalue()
    assert first_output

    buffer.truncate(0)
    buffer.seek(0)

    resize_event = input_base.ResizeEvent(width=80, height=24)
    terminal._handle_input_event(resize_event)
    await terminal.render()

    output = buffer.getvalue()
    assert output == first_output
    assert tui_controls.ERASE_SCROLLBACK in output


def test_component_style_panel_extended_properties() -> None:
    component = DummyComponent("body")
    style = tui_terminal.ComponentStyle(
        panel_title="Title",
        panel_subtitle="Subtitle",
        panel_style="on blue",
        panel_border_style="red",
        panel_title_highlight=True,
    )
    component.component_style = style

    styled = component.apply_style("content")
    assert isinstance(styled, rich_panel.Panel)

    panel = styled
    title = panel.title
    subtitle = panel.subtitle

    assert title is not None
    assert str(title) == "Title"
    assert subtitle is not None
    assert str(subtitle) == "Subtitle"


def test_component_style_padding_uses_new_fields() -> None:
    component = DummyComponent("body")
    style = tui_terminal.ComponentStyle(
        padding_pad=2,
        padding_style="green",
    )
    component.component_style = style

    styled = component.apply_style("x")
    assert isinstance(styled, rich_padding.Padding)

    padding = styled
    assert padding.renderable == "x" or isinstance(
        padding.renderable, rich_console.RenderableType
    )


def test_component_style_bottom_margin_adds_empty_lines() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        width=20,
    )
    component = DummyComponent("body")
    style = tui_terminal.ComponentStyle(margin_bottom=2)
    component.component_style = style

    styled = component.apply_style("x")
    options = console.options
    lines = console.render_lines(
        styled,
        options=options,
        pad=False,
        new_lines=False,
    )

    assert len(lines) == 3


@pytest.mark.asyncio
async def test_tui_state_ctrl_c_triggers_on_stop() -> None:
    async def on_input(_: str) -> None:
        return

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return

    called: list[object] = []

    async def on_stop() -> None:
        called.append(object())

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=None,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=on_stop,
        on_eof=None,
    )
    event = input_base.KeyEvent(action="down", key="c", ctrl=True)
    ui_state._input_handler.publish(event)
    await asyncio.sleep(0)
    assert called


@pytest.mark.asyncio
async def test_tui_state_ctrl_dot_collapses_last_messages_progressively() -> None:
    async def on_input(_: str) -> None:
        return

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=None,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )

    for i in range(12):
        ui_state.add_rich_text(f"msg {i}")

    message_components = ui_state.terminal.components[1:-2]
    last_ten = message_components[-10:]
    assert last_ten
    assert all(c.supports_collapse for c in last_ten)
    assert all(c.is_expanded for c in last_ten)

    open_cmd = input_base.KeyEvent(action="down", key="x", ctrl=True)
    collapse = input_base.KeyEvent(action="down", key="c")
    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(collapse)
    assert ui_state._action_stack[-1].kind is not tui_uistate.ActionKind.COMMAND_MANAGER

    assert all(c.is_collapsed for c in last_ten)
    ui_state.terminal._delete_removed_components()

    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(collapse)
    assert ui_state._action_stack[-1].kind is not tui_uistate.ActionKind.COMMAND_MANAGER

    last_twenty = message_components[-20:]
    assert last_twenty
    assert all(c.supports_collapse for c in last_twenty)
    assert all(c.is_collapsed for c in last_twenty)


@pytest.mark.asyncio
async def test_tui_state_ctrl_comma_expands_last_messages_progressively_and_resets_on_other_key() -> (
    None
):
    async def on_input(_: str) -> None:
        return

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=None,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )

    for i in range(25):
        ui_state.add_rich_text(f"msg {i}")

    message_components = ui_state.terminal.components[1:-2]
    assert len(message_components) == 25

    open_cmd = input_base.KeyEvent(action="down", key="x", ctrl=True)
    collapse = input_base.KeyEvent(action="down", key="c")
    expand = input_base.KeyEvent(action="down", key="e")
    other = input_base.KeyEvent(action="down", key="x", text="x")

    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(collapse)
    # Command manager closed; clean up removed components before reopening.
    ui_state.terminal._delete_removed_components()
    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(collapse)
    assert ui_state._action_stack[-1].kind is not tui_uistate.ActionKind.COMMAND_MANAGER

    last_twenty = message_components[-20:]
    assert last_twenty
    assert all(c.supports_collapse for c in last_twenty)
    assert all(c.is_collapsed for c in last_twenty)

    ui_state.terminal._delete_removed_components()

    ui_state._input_handler.publish(other)
    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(expand)
    assert ui_state._action_stack[-1].kind is not tui_uistate.ActionKind.COMMAND_MANAGER

    last_ten = message_components[-10:]
    ten_before = message_components[-20:-10]
    assert all(c.is_expanded for c in last_ten)
    assert all(c.is_collapsed for c in ten_before)
    ui_state.terminal._delete_removed_components()
    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(expand)
    assert all(c.is_expanded for c in last_twenty)


@pytest.mark.asyncio
async def test_tui_state_ctrl_x_opens_command_manager_esc_closes_and_hotkey_executes() -> (
    None
):
    async def on_input(_: str) -> None:
        return

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return

    called: list[object] = []

    async def on_open_logs() -> None:
        called.append(object())

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=None,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_open_logs=on_open_logs,
        on_stop=None,
        on_eof=None,
    )

    open_cmd = input_base.KeyEvent(action="down", key="x", ctrl=True)
    esc = input_base.KeyEvent(action="down", key="esc")
    ui_state._input_handler.publish(esc)
    assert ui_state._action_stack[-1].kind is not tui_uistate.ActionKind.COMMAND_MANAGER

    ui_state._input_handler.publish(open_cmd)
    assert ui_state._action_stack[-1].kind is tui_uistate.ActionKind.COMMAND_MANAGER

    ui_state._input_handler.publish(open_cmd)
    assert ui_state._action_stack[-1].kind is not tui_uistate.ActionKind.COMMAND_MANAGER
    ui_state.terminal._delete_removed_components()

    ui_state._input_handler.publish(open_cmd)
    assert ui_state._action_stack[-1].kind is tui_uistate.ActionKind.COMMAND_MANAGER

    open_logs = input_base.KeyEvent(action="down", key="l")
    ui_state._input_handler.publish(open_logs)
    await asyncio.sleep(0)
    assert called
    assert ui_state._action_stack[-1].kind is not tui_uistate.ActionKind.COMMAND_MANAGER


@pytest.mark.asyncio
async def test_tui_state_ctrl_d_triggers_on_eof() -> None:
    async def on_input(_: str) -> None:
        return

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return

    called: list[object] = []

    async def on_eof() -> None:
        called.append(object())

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=None,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=on_eof,
    )
    event = input_base.KeyEvent(action="down", key="d", ctrl=True)
    ui_state._input_handler.publish(event)
    await asyncio.sleep(0)
    assert called


@pytest.mark.asyncio
async def test_component_visibility_full_and_incremental_render() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        height=3,
    )
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)

    top = MultiLineComponent(["top1", "top2"], id="top")
    bottom = MultiLineComponent(["bottom1", "bottom2"], id="bottom")

    terminal.append_component(top)
    terminal.append_component(bottom)

    await terminal.render()

    assert top.is_visible
    assert bottom.is_visible

    bottom.lines.append("bottom3")
    terminal.notify_component(bottom)

    await terminal.render()

    assert not top.is_visible
    assert bottom.is_visible


def test_terminal_register_and_remove_animation_component() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)

    component = DummyComponent("hello")
    terminal.append_component(component)

    terminal.register_animation(component)
    assert component in terminal._animation_components

    terminal.remove_component(component)
    assert component not in terminal._animation_components


def test_animation_tick_marks_only_visible_components_dirty() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=False)
    terminal = tui_terminal.Terminal(console=console, settings=settings)

    component = DummyComponent("hello")
    terminal.append_component(component)
    terminal.register_animation(component)

    component.is_visible = False
    terminal._dirty_components.clear()
    terminal._animation_tick()
    assert component not in terminal._dirty_components

    component.is_visible = True
    terminal._dirty_components.clear()
    terminal._animation_tick()
    assert component in terminal._dirty_components


@pytest.mark.asyncio
async def test_terminal_animation_worker_lifecycle() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    settings = tui_terminal.TerminalSettings(auto_render=True, min_render_interval_ms=0)
    terminal = tui_terminal.Terminal(console=console, settings=settings)

    component = DummyComponent("hello")
    terminal.append_component(component)
    terminal.register_animation(component)

    assert terminal._animation_task is None

    await terminal.start()
    animation_task = terminal._animation_task
    assert animation_task is not None
    assert not animation_task.done()

    await terminal.stop()
    assert terminal._animation_task is None
