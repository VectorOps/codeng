from __future__ import annotations

import io

from rich import console as rich_console

from vocode import models, state
from vocode.tui import uistate as tui_uistate
from vocode.tui.lib.input import base as input_base


def test_tui_state_deletes_steps_by_id() -> None:
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

    execution = state.WorkflowExecution(workflow_name="wf-step-delete")
    node_execution = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    )
    execution.node_executions[node_execution.id] = node_execution

    step1 = state.Step(
        execution=node_execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="hello",
        ),
        is_complete=True,
    )
    step2 = state.Step(
        execution=node_execution,
        type=state.StepType.INPUT_MESSAGE,
        message=state.Message(
            role=models.Role.USER,
            text="user",
        ),
        is_complete=True,
    )

    ui_state.handle_step(step1)
    ui_state.handle_step(step2)

    terminal = ui_state.terminal
    assert terminal.get_component(str(step1.id)) is not None
    assert terminal.get_component(str(step2.id)) is not None

    ui_state.handle_step_deleted([str(step1.id)])
    try:
        terminal.get_component(str(step1.id))
        assert False
    except KeyError:
        pass
    assert terminal.get_component(str(step2.id)) is not None
