from __future__ import annotations

import asyncio
import io

import pytest
from rich import console as rich_console

from vocode.tui import uistate as tui_uistate
from vocode.manager import proto as manager_proto
from vocode.tui.lib.input import base as input_base


@pytest.mark.asyncio
async def test_tui_state_triggers_autocomplete_request_on_cursor_move() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    requests: list[tuple[str, int, int]] = []

    async def on_input(_: str) -> None:
        return None
    async def on_autocomplete(text: str, row: int, col: int) -> None:
        requests.append((text, row, col))

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=on_autocomplete,
        on_stop=None,
        on_eof=None,
    )
    component = ui_state.terminal.components[-2]
    component.text = "hello"
    left_event = input_base.KeyEvent(action="down", key="left")
    component.on_key_event(left_event)
    await asyncio.sleep(tui_uistate.AUTOCOMPLETE_DEBOUNCE_MS / 1000.0 + 0.05)
    assert requests


def test_tui_state_autocomplete_stack_and_toolbar() -> None:
    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=None,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )
    terminal = ui_state.terminal
    assert len(terminal.components) == 3
    toolbar = terminal.components[-1]

    items = [
        manager_proto.AutocompleteItem(title="one", value="ONE"),
        manager_proto.AutocompleteItem(title="two", value="TWO"),
    ]
    ui_state.handle_autocomplete_options(items)
    assert len(terminal.components) == 3
    new_toolbar = terminal.components[-1]
    assert new_toolbar is not toolbar

    ui_state.handle_autocomplete_options(None)
    assert len(terminal.components) == 3
    restored_toolbar = terminal.components[-1]
    assert restored_toolbar is not new_toolbar


def test_tui_state_run_autocomplete_after_run_with_space() -> None:
    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=None,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )
    terminal = ui_state.terminal
    input_component = terminal.components[-2]
    input_component.text = "/run "
    input_component.set_cursor_position(0, len("/run "))

    items = [
        manager_proto.AutocompleteItem(
            title="/run wf-auto - workflow",
            value="wf-auto",
        )
    ]
    ui_state.handle_autocomplete_options(items)

    select_component = terminal.components[-1]
    select_component.select_current()

    assert input_component.text == "/run wf-auto"


def test_tui_state_run_autocomplete_after_run_without_space() -> None:
    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=None,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )
    terminal = ui_state.terminal
    input_component = terminal.components[-2]
    input_component.text = "/run"
    input_component.set_cursor_position(0, len("/run"))

    items = [
        manager_proto.AutocompleteItem(
            title="/run wf-auto - workflow",
            value="wf-auto",
        )
    ]
    ui_state.handle_autocomplete_options(items)

    select_component = terminal.components[-1]
    select_component.select_current()

    assert input_component.text == "/run wf-auto"
