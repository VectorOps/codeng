from __future__ import annotations

import io

from rich import console as rich_console

from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.lib.input import base as input_base
from vocode.tui.screens import base_viewer


def _make_terminal(
    width: int, height: int
) -> tuple[tui_terminal.Terminal, io.StringIO]:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        width=width,
        height=height,
    )
    terminal = tui_terminal.Terminal(console=console)
    return terminal, buffer


def test_base_viewer_renders_with_footer_space() -> None:
    lines = "\n".join(str(i) for i in range(10))
    terminal, buffer = _make_terminal(20, 10)
    screen = base_viewer.TextViewerScreen(terminal, lines)
    terminal.push_screen(screen)
    output = buffer.getvalue().splitlines()
    assert len(output) <= 11


def test_base_viewer_basic_scrolling_and_quit() -> None:
    lines = "\n".join(str(i) for i in range(100))
    terminal, buffer = _make_terminal(20, 10)
    screen = base_viewer.TextViewerScreen(terminal, lines)
    terminal.push_screen(screen)

    down = input_base.KeyEvent(action="down", key="down")
    screen.on_key_event(down)
    screen.on_key_event(down)

    q = input_base.KeyEvent(action="down", key="q")
    screen.on_key_event(q)

    output = buffer.getvalue()
    assert output


def test_base_viewer_search_and_next() -> None:
    text = "one\ntwo target\nthree target\n"
    terminal, buffer = _make_terminal(40, 8)
    screen = base_viewer.TextViewerScreen(terminal, text)
    terminal.push_screen(screen)

    slash = input_base.KeyEvent(action="down", key="/", text="/")
    screen.on_key_event(slash)
    for ch in "target":
        event = input_base.KeyEvent(action="down", key=ch, text=ch)
        screen.on_key_event(event)
    enter = input_base.KeyEvent(action="down", key="enter", text="\n")
    screen.on_key_event(enter)

    n_event = input_base.KeyEvent(action="down", key="n")
    screen.on_key_event(n_event)

    output = buffer.getvalue()
    assert "target" in output


def test_base_viewer_initial_position_bottom() -> None:
    lines = "\n".join(str(i) for i in range(20))
    terminal, buffer = _make_terminal(20, 10)
    screen = base_viewer.TextViewerScreen(terminal, lines)
    terminal.push_screen(screen)
    output = buffer.getvalue().splitlines()
    assert any("19" in line for line in output)


def test_base_viewer_initial_position_top() -> None:
    lines = "\n".join(str(i) for i in range(20))
    terminal, buffer = _make_terminal(20, 10)
    screen = base_viewer.TextViewerScreen(
        terminal, lines, initial_bottom=False
    )
    terminal.push_screen(screen)
    output = buffer.getvalue().splitlines()
    assert any("0" in line for line in output)


def test_base_viewer_home_end_keys() -> None:
    lines = "\n".join(str(i) for i in range(50))
    terminal, buffer = _make_terminal(20, 10)
    screen = base_viewer.TextViewerScreen(terminal, lines)
    terminal.push_screen(screen)

    home = input_base.KeyEvent(action="down", key="home")
    screen.on_key_event(home)
    after_home = buffer.getvalue()

    end = input_base.KeyEvent(action="down", key="end")
    screen.on_key_event(end)
    after_end = buffer.getvalue()

    assert after_home != after_end


def test_no_jitter_when_pressing_up_at_top() -> None:
    lines = "\n".join(str(i) for i in range(50))
    terminal, buffer = _make_terminal(20, 10)
    screen = base_viewer.TextViewerScreen(terminal, lines)
    terminal.push_screen(screen)

    home = input_base.KeyEvent(action="down", key="home")
    screen.on_key_event(home)
    before_up = buffer.getvalue()

    up = input_base.KeyEvent(action="down", key="up")
    screen.on_key_event(up)
    after_up = buffer.getvalue()

    assert before_up == after_up
