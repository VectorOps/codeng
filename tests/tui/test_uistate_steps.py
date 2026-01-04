from __future__ import annotations

import io
from uuid import uuid4

import pytest
from rich import console as rich_console

from vocode import models, state
from vocode.tui import uistate as tui_uistate
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
    )
    terminal = ui_state.terminal
    assert len(terminal.components) == 2

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
    assert len(components) == 3
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
    assert len(components_after_update) == 3
    assert components_after_update[1] is step_component
    assert step_component.markdown == "second"

    step_no_message = state.Step(
        id=uuid4(),
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=None,
    )

    ui_state.handle_step(step_no_message)
    assert len(terminal.components) == 3

    empty_message = state.Message(role=models.Role.USER, text="\n\n")
    step_empty_text = state.Step(
        id=step_id,
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=empty_message,
    )

    ui_state.handle_step(step_empty_text)
    assert len(terminal.components) == 3
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
    )
    terminal = ui_state.terminal
    assert len(terminal.components) == 2

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

    assert len(terminal.components) == 2
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
    )
    terminal = ui_state.terminal
    assert len(terminal.components) == 2

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
    assert len(components) == 3
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
    assert len(components_after_update) == 3
    assert components_after_update[1] is step_component
    assert step_component.markdown == "interim"

    await terminal.render()
    output = buffer.getvalue()
    assert "interim" in output
    assert "final" not in output
