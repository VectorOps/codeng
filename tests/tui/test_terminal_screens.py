from __future__ import annotations

import asyncio
import io

from rich import console as rich_console

from vocode.tui import lib as tui_terminal
from vocode.tui.lib import controls as tui_controls
from vocode.tui.lib.input import base as input_base
import pytest


class DummyScreen:
    def __init__(self, terminal: tui_terminal.Terminal) -> None:
        self.render_count = 0
        self.key_events: list[input_base.KeyEvent] = []
        self._terminal = terminal

    def render(self) -> None:
        self.render_count += 1
        self._terminal.console.print("screen", end="")

    def on_key_event(self, event: input_base.KeyEvent) -> None:
        self.key_events.append(event)


@pytest.mark.asyncio
async def test_push_and_pop_screen_alt_buffer_and_render() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    terminal = tui_terminal.Terminal(console=console)

    screen = DummyScreen(terminal)
    terminal.push_screen(screen)

    output = buffer.getvalue()
    assert tui_controls.ALT_SCREEN_ENTER in output
    assert "screen" in output
    assert screen.render_count == 1

    buffer.truncate(0)
    buffer.seek(0)

    popped = terminal.pop_screen()
    assert popped is screen

    output = buffer.getvalue()
    assert tui_controls.ALT_SCREEN_EXIT in output


@pytest.mark.asyncio
async def test_top_screen_receives_key_events() -> None:
    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return

    handler = DummyInputHandler()
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    terminal = tui_terminal.Terminal(console=console, input_handler=handler)

    screen = DummyScreen(terminal)
    terminal.push_screen(screen)

    event = input_base.KeyEvent(action="down", key="a", text="a")
    handler.publish(event)
    await asyncio.sleep(0)

    assert screen.key_events == [event]
