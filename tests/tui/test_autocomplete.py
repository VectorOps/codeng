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
    ui_state._input_handler.publish(left_event)
    await asyncio.sleep(0.05)
    assert requests


@pytest.mark.asyncio
async def test_tui_state_autocomplete_debounce_does_not_cancel_active_task() -> None:
    requests: list[tuple[str, int, int]] = []
    got_request = asyncio.Event()

    async def on_input(_: str) -> None:
        return None

    async def on_autocomplete(text: str, row: int, col: int) -> None:
        requests.append((text, row, col))
        got_request.set()

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=None,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=on_autocomplete,
        on_stop=None,
        on_eof=None,
    )

    input_component = ui_state.terminal.components[-2]

    input_component.text = "a"
    input_component.set_cursor_position(0, 1)
    ui_state._handle_cursor_event(0, 1)

    await asyncio.sleep(0.05)
    input_component.text = "ab"
    input_component.set_cursor_position(0, 2)
    ui_state._handle_cursor_event(0, 2)

    await asyncio.sleep(0.05)
    input_component.text = "abc"
    input_component.set_cursor_position(0, 3)
    ui_state._handle_cursor_event(0, 3)

    await asyncio.sleep(0.05)
    input_component.text = "abcd"
    input_component.set_cursor_position(0, 4)
    ui_state._handle_cursor_event(0, 4)

    await asyncio.wait_for(got_request.wait(), timeout=0.35)
    assert requests[0] == ("a", 0, 1)

    await asyncio.sleep(tui_uistate.AUTOCOMPLETE_DEBOUNCE_MS / 1000.0 + 0.1)
    assert requests[-1] == ("abcd", 0, 4)
    assert len(requests) == 3


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
        manager_proto.AutocompleteItem(
            title="one",
            replace_start=0,
            replace_text="",
            insert_text="ONE",
        ),
        manager_proto.AutocompleteItem(
            title="two",
            replace_start=0,
            replace_text="",
            insert_text="TWO",
        ),
    ]
    ui_state.handle_autocomplete_options(items)
    terminal._delete_removed_components()
    assert len(terminal.components) == 3
    new_toolbar = terminal.components[-1]
    assert new_toolbar is not toolbar

    ui_state.handle_autocomplete_options(None)
    terminal._delete_removed_components()
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
            replace_start=0,
            replace_text="/run ",
            insert_text="/run wf-auto",
        )
    ]
    ui_state.handle_autocomplete_options(items)

    select_component = terminal.components[-1]
    down_event = input_base.KeyEvent(action="down", key="down")
    select_component.on_key_event(down_event)
    enter_event = input_base.KeyEvent(action="down", key="enter")
    select_component.on_key_event(enter_event)

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
            replace_start=0,
            replace_text="/run",
            insert_text="/run wf-auto",
        )
    ]
    ui_state.handle_autocomplete_options(items)

    select_component = terminal.components[-1]
    down_event = input_base.KeyEvent(action="down", key="down")
    select_component.on_key_event(down_event)
    enter_event = input_base.KeyEvent(action="down", key="enter")
    select_component.on_key_event(enter_event)

    assert input_component.text == "/run wf-auto"


def test_tui_state_autocomplete_apply_noop_on_mismatch() -> None:
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
    input_component.text = "hello"
    input_component.set_cursor_position(0, len("hello"))

    items = [
        manager_proto.AutocompleteItem(
            title="world",
            replace_start=0,
            replace_text="nope",
            insert_text="world",
        )
    ]
    ui_state.handle_autocomplete_options(items)
    select_component = terminal.components[-1]
    down_event = input_base.KeyEvent(action="down", key="down")
    select_component.on_key_event(down_event)
    enter_event = input_base.KeyEvent(action="down", key="enter")
    select_component.on_key_event(enter_event)
    assert input_component.text == "hello"


def test_tui_state_file_autocomplete_selection_removes_at_prefix() -> None:
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
    input_component.text = "@re"
    input_component.set_cursor_position(0, len("@re"))

    items = [
        manager_proto.AutocompleteItem(
            title="repo/file.py",
            replace_start=0,
            replace_text="@re",
            insert_text="repo/file.py",
        )
    ]
    ui_state.handle_autocomplete_options(items)
    select_component = terminal.components[-1]
    down_event = input_base.KeyEvent(action="down", key="down")
    select_component.on_key_event(down_event)
    enter_event = input_base.KeyEvent(action="down", key="enter")
    select_component.on_key_event(enter_event)
    assert input_component.text == "repo/file.py"


def test_tui_state_autocomplete_selection_uses_latest_items() -> None:
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

    input_component.text = "/ru"
    input_component.set_cursor_position(0, len("/ru"))

    ui_state.handle_autocomplete_options(
        [
            manager_proto.AutocompleteItem(
                title="/continue",
                replace_start=0,
                replace_text="/",
                insert_text="/continue ",
            )
        ]
    )

    ui_state.handle_autocomplete_options(
        [
            manager_proto.AutocompleteItem(
                title="/run",
                replace_start=0,
                replace_text="/ru",
                insert_text="/run ",
            )
        ]
    )

    select_component = terminal.components[-1]
    down_event = input_base.KeyEvent(action="down", key="down")
    select_component.on_key_event(down_event)
    enter_event = input_base.KeyEvent(action="down", key="enter")
    select_component.on_key_event(enter_event)
    assert input_component.text == "/run "


def test_tui_state_autocomplete_apply_insert_when_replace_text_empty() -> None:
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
    input_component.text = "he"
    input_component.set_cursor_position(0, 2)

    items = [
        manager_proto.AutocompleteItem(
            title="hello",
            replace_start=2,
            replace_text="",
            insert_text="llo",
        )
    ]
    ui_state.handle_autocomplete_options(items)
    select_component = terminal.components[-1]
    down_event = input_base.KeyEvent(action="down", key="down")
    select_component.on_key_event(down_event)
    enter_event = input_base.KeyEvent(action="down", key="enter")
    select_component.on_key_event(enter_event)
    assert input_component.text == "hello"
