from __future__ import annotations

import io
import typing
from uuid import uuid4

import pytest
from rich import console as rich_console

from vocode import models, settings, state
from vocode.tui import tcf as tui_tcf
from vocode.tui import uistate as tui_uistate
from vocode.tui.lib.input import base as input_base


@tui_tcf.ToolCallFormatterManager.register("test-multiline")
class _TestMultilineToolCallFormatter(tui_tcf.BaseToolCallFormatter):
    def format_input(
        self,
        terminal,
        tool_name: str,
        arguments: typing.Any,
        config,
    ):
        return None

    def format_output(
        self,
        terminal,
        tool_name: str,
        result: typing.Any,
        config,
    ):
        if isinstance(result, dict) and "text" in result:
            return str(result["text"])
        return str(result)


@pytest.mark.asyncio
async def test_tool_call_resp_component_appends_suffix_when_collapsed_and_trimmed() -> (
    None
):
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        height=50,
    )

    tui_tcf.ToolCallFormatterManager.configure(
        settings.Settings(
            tool_call_formatters={
                "tool": settings.ToolCallFormatter(
                    title="tool",
                    formatter="test-multiline",
                    show_output=True,
                )
            }
        )
    )

    async def on_input(_: str) -> None:
        return

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    output = "\n".join([f"line {i}" for i in range(15)])
    resp = state.ToolCallResp(
        id="call_1",
        name="tool",
        result={"text": output},
    )
    resp_step = state.Step(
        id=uuid4(),
        execution=execution,
        type=state.StepType.TOOL_RESULT,
        message=state.Message(
            role=models.Role.TOOL,
            text="",
            tool_call_responses=[resp],
        ),
    )
    ui_state.handle_step(resp_step)

    component = ui_state.terminal.get_component(str(resp_step.id))
    lines = component.render(console.options)
    rendered = "\n".join("".join(seg.text for seg in line) for line in lines)
    assert "... (5 other lines" in rendered
