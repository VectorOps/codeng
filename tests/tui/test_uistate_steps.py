from __future__ import annotations

import io
from uuid import uuid4

import pytest
from rich import console as rich_console

from vocode import models, state
from vocode import settings as vocode_settings
from vocode.tui import uistate as tui_uistate
from vocode.tui.components import tool_call_req as tool_call_req_component
from vocode.tui.lib.components import markdown_component as tui_markdown_component
from vocode.tui.lib.input import base as input_base


@pytest.mark.asyncio
async def test_tui_state_inserts_and_updates_step_markdown() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )
    terminal = ui_state.terminal
    assert len(terminal.components) == 3

    execution = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    )
    step_id = uuid4()
    message1 = state.Message(role=models.Role.USER, text="first")
    step1 = state.Step(
        id=step_id,
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=message1,
    )

    ui_state.handle_step(step1)

    components = terminal.components
    assert len(components) == 4
    header = components[0]
    step_component = components[1]
    input_component = components[2]

    assert header is not step_component
    assert input_component is not step_component
    assert isinstance(step_component, tui_markdown_component.MarkdownComponent)
    assert step_component.markdown == "first"

    message2 = state.Message(role=models.Role.USER, text="second\n\n")
    step2 = state.Step(
        id=step_id,
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=message2,
    )

    ui_state.handle_step(step2)

    components_after_update = terminal.components
    assert len(components_after_update) == 4
    assert components_after_update[1] is step_component
    assert step_component.markdown == "second"

    step_no_message = state.Step(
        id=uuid4(),
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=None,
    )

    ui_state.handle_step(step_no_message)
    assert len(terminal.components) == 4

    empty_message = state.Message(role=models.Role.USER, text="\n\n")
    step_empty_text = state.Step(
        id=step_id,
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=empty_message,
    )

    ui_state.handle_step(step_empty_text)
    assert len(terminal.components) == 4
    assert step_component.markdown == ""

    await terminal.render()
    output = buffer.getvalue()
    assert "second" not in output
    assert "first" not in output


@pytest.mark.asyncio
async def test_tui_state_hides_all_output_mode_hide_all() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )
    terminal = ui_state.terminal
    assert len(terminal.components) == 3

    execution = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    )
    step = state.Step(
        id=uuid4(),
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.USER, text="hidden"),
        output_mode=models.OutputMode.HIDE_ALL,
    )

    ui_state.handle_step(step)

    assert len(terminal.components) == 3
    await terminal.render()
    output = buffer.getvalue()
    assert "hidden" not in output


@pytest.mark.asyncio
async def test_tui_state_hides_final_output_mode_hide_final() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )
    terminal = ui_state.terminal
    assert len(terminal.components) == 3

    execution = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    )
    step_id = uuid4()
    message1 = state.Message(role=models.Role.USER, text="interim")
    step1 = state.Step(
        id=step_id,
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=message1,
        output_mode=models.OutputMode.HIDE_FINAL,
    )

    ui_state.handle_step(step1)

    components = terminal.components
    assert len(components) == 4
    step_component = components[1]
    assert isinstance(step_component, tui_markdown_component.MarkdownComponent)
    assert step_component.markdown == "interim"

    message2 = state.Message(role=models.Role.USER, text="final")
    step2 = state.Step(
        id=step_id,
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=message2,
        is_final=True,
        output_mode=models.OutputMode.HIDE_FINAL,
    )

    ui_state.handle_step(step2)

    components_after_update = terminal.components
    assert len(components_after_update) == 4
    assert components_after_update[1] is step_component
    assert step_component.markdown == "interim"

    await terminal.render()
    output = buffer.getvalue()
    assert "interim" in output
    assert "final" not in output


@pytest.mark.asyncio
async def test_tui_state_renders_rejection_steps() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )
    terminal = ui_state.terminal

    execution = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    )

    rejection_message = state.Message(
        role=models.Role.USER,
        text="Rejected because of reasons.",
    )
    rejection_step = state.Step(
        execution=execution,
        type=state.StepType.REJECTION,
        message=rejection_message,
    )
    ui_state.handle_step(rejection_step)

    await terminal.render()
    output = buffer.getvalue()
    assert "Rejected because of reasons." in output


@pytest.mark.asyncio
async def test_tool_request_with_confirmation_expanded_by_default() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )

    execution = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    )
    req = state.ToolCallReq(
        id="call_1",
        name="tool",
        arguments={},
        status=state.ToolCallReqStatus.REQUIRES_CONFIRMATION,
    )
    step = state.Step(
        id=uuid4(),
        execution=execution,
        type=state.StepType.TOOL_REQUEST,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[req],
        ),
    )

    ui_state.handle_step(step)

    components = ui_state.terminal.components
    tool_components = [
        c
        for c in components
        if isinstance(c, tool_call_req_component.ToolCallReqComponent)
    ]
    assert len(tool_components) == 1
    assert tool_components[0].is_expanded


@pytest.mark.asyncio
async def test_tui_state_history_up_places_cursor_on_last_row() -> None:
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
    ui_state.history.add("first line\nsecond line")
    input_component = ui_state.terminal.components[-2]
    event = input_base.KeyEvent(action="down", key="up")
    ui_state._input_handler.publish(event)
    assert input_component.cursor_row == len(input_component.lines) - 1


@pytest.mark.asyncio
async def test_tui_state_history_down_places_cursor_on_first_row() -> None:
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
    ui_state.history.add("one\nTWO")
    ui_state.history.add("alpha\nbeta")
    input_component = ui_state.terminal.components[-2]
    event_up = input_base.KeyEvent(action="down", key="up")
    ui_state._input_handler.publish(event_up)
    ui_state._input_handler.publish(event_up)
    ui_state._input_handler.publish(event_up)
    assert input_component.cursor_row == len(input_component.lines) - 1
    event_down = input_base.KeyEvent(action="down", key="down")
    ui_state._input_handler.publish(event_down)
    assert input_component.cursor_row == 0


@pytest.mark.asyncio
async def test_tool_request_with_confirmation_collapsed_when_disabled() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    tui_opts = vocode_settings.TUIOptions(
        expand_confirm_tools=False,
    )

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
        tui_options=tui_opts,
    )

    execution = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    )
    req = state.ToolCallReq(
        id="call_1",
        name="tool",
        arguments={},
        status=state.ToolCallReqStatus.REQUIRES_CONFIRMATION,
    )
    step = state.Step(
        id=uuid4(),
        execution=execution,
        type=state.StepType.TOOL_REQUEST,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[req],
        ),
    )

    ui_state.handle_step(step)

    components = ui_state.terminal.components
    tool_components = [
        c
        for c in components
        if isinstance(c, tool_call_req_component.ToolCallReqComponent)
    ]
    assert len(tool_components) == 1
    assert tool_components[0].is_collapsed


@pytest.mark.asyncio
async def test_tool_request_update_plan_uses_formatter_default_for_stats() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )

    execution = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    )
    req = state.ToolCallReq(
        id="call_1",
        name="update_plan",
        arguments={},
        status=state.ToolCallReqStatus.PENDING_EXECUTION,
    )
    step = state.Step(
        id=uuid4(),
        execution=execution,
        type=state.StepType.TOOL_REQUEST,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[req],
        ),
    )

    ui_state.handle_step(step)

    components = ui_state.terminal.components
    tool_components = [
        c
        for c in components
        if isinstance(c, tool_call_req_component.ToolCallReqComponent)
    ]
    assert len(tool_components) == 1
    assert tool_components[0].show_execution_stats is False


@pytest.mark.asyncio
async def test_tool_request_confirmation_renders_autoapprove_hint() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )

    execution = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    )
    req = state.ToolCallReq(
        id="call_1",
        name="tool",
        arguments={},
        status=state.ToolCallReqStatus.REQUIRES_CONFIRMATION,
    )
    step = state.Step(
        id=uuid4(),
        execution=execution,
        type=state.StepType.TOOL_REQUEST,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[req],
        ),
    )
    ui_state.handle_step(step)

    await ui_state.terminal.render()
    output = buffer.getvalue()
    assert "/aa" not in output
