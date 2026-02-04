from __future__ import annotations

from uuid import uuid4

import pytest

from vocode import models, state
from vocode.tui import uistate as tui_uistate
from vocode.tui.components import tool_call_req as tool_call_req_component
from vocode.tui.lib.input import base as input_base


@pytest.mark.asyncio
async def test_ctrl_shift_dot_collapses_tool_steps_progressively() -> None:
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    for i in range(12):
        req = state.ToolCallReq(
            id=f"call_{i}",
            name="tool",
            arguments={"i": i},
        )
        resp = state.ToolCallResp(
            id=f"call_{i}",
            name="tool",
            result={"ok": True, "i": i},
        )
        req_step = state.Step(
            id=uuid4(),
            execution=execution,
            type=state.StepType.TOOL_REQUEST,
            message=state.Message(
                role=models.Role.ASSISTANT,
                text="",
                tool_call_requests=[req],
                tool_call_responses=[resp],
            ),
        )
        ui_state.handle_step(req_step)

    message_components = ui_state.terminal.components[1:-2]
    tool_components = [
        c
        for c in message_components
        if isinstance(c, tool_call_req_component.ToolCallReqComponent)
    ]
    assert len(tool_components) == 12
    assert all(c.supports_collapse for c in tool_components)

    for component in tool_components:
        component.set_collapsed(False)

    non_tool_components = [c for c in message_components if c not in tool_components]
    assert non_tool_components
    assert all(c.is_expanded for c in non_tool_components if c.supports_collapse)

    open_cmd = input_base.KeyEvent(action="down", key="space", ctrl=True)
    collapse = input_base.KeyEvent(action="down", key="c", shift=True)
    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(collapse)
    assert ui_state._action_stack[-1].kind is not tui_uistate.ActionKind.COMMAND_MANAGER
    assert all(c.is_collapsed for c in tool_components[-10:])
    assert all(c.is_expanded for c in tool_components[:-10])
    # Command manager closed; clean up removed components before reopening.
    ui_state.terminal._delete_removed_components()

    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(collapse)
    assert ui_state._action_stack[-1].kind is not tui_uistate.ActionKind.COMMAND_MANAGER
    assert all(c.is_collapsed for c in tool_components[-20:])


@pytest.mark.asyncio
async def test_ctrl_shift_comma_expands_tool_steps_progressively_and_resets_on_other_key() -> (
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    for i in range(25):
        req = state.ToolCallReq(
            id=f"call_{i}",
            name="tool",
            arguments={"i": i},
        )
        req_step = state.Step(
            id=uuid4(),
            execution=execution,
            type=state.StepType.TOOL_REQUEST,
            message=state.Message(
                role=models.Role.ASSISTANT,
                text="",
                tool_call_requests=[req],
            ),
        )
        ui_state.handle_step(req_step)

    message_components = ui_state.terminal.components[1:-2]
    tool_components = [
        c
        for c in message_components
        if isinstance(c, tool_call_req_component.ToolCallReqComponent)
    ]
    assert len(tool_components) == 25
    assert all(c.supports_collapse for c in tool_components)

    open_cmd = input_base.KeyEvent(action="down", key="space", ctrl=True)
    collapse = input_base.KeyEvent(action="down", key="c", shift=True)
    expand = input_base.KeyEvent(action="down", key="e", shift=True)
    other = input_base.KeyEvent(action="down", key="x", text="x")
    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(collapse)
    ui_state.terminal._delete_removed_components()

    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(collapse)
    ui_state.terminal._delete_removed_components()
    assert ui_state._action_stack[-1].kind is not tui_uistate.ActionKind.COMMAND_MANAGER
    last_twenty = tool_components[-20:]
    assert all(c.is_collapsed for c in last_twenty)

    ui_state._input_handler.publish(other)
    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(expand)
    ui_state.terminal._delete_removed_components()
    assert ui_state._action_stack[-1].kind is not tui_uistate.ActionKind.COMMAND_MANAGER
    last_ten = tool_components[-10:]
    ten_before = tool_components[-20:-10]
    assert all(c.is_expanded for c in last_ten)
    assert all(c.is_collapsed for c in ten_before)

    ui_state._input_handler.publish(open_cmd)
    ui_state._input_handler.publish(expand)
    assert all(c.is_expanded for c in last_twenty)
