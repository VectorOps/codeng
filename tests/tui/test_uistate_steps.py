from __future__ import annotations

import io
from uuid import uuid4
import datetime

import pyfiglet
import pytest
from rich import console as rich_console

from vocode import models, state
from vocode.history.manager import HistoryManager
from vocode import settings as vocode_settings
from vocode.tui import styles as tui_styles
from vocode.tui import uistate as tui_uistate
from vocode.tui.components import tool_call_req as tool_call_req_component
from vocode.tui.lib.components import markdown_component as tui_markdown_component
from vocode.tui.lib.components import renderable as tui_renderable_component
from vocode.tui.lib.components import rich_text_component as tui_rich_text_component
from vocode.tui.lib.components import step_output_component as tui_step_output_component
from vocode.tui.lib.input import base as input_base


def _make_step(
    execution: state.NodeExecution,
    *,
    step_id=None,
    step_type: state.StepType,
    message: state.Message | None = None,
    output_mode: models.OutputMode = models.OutputMode.SHOW,
    is_final: bool = False,
) -> state.Step:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    local_execution = execution.model_copy(
        update={
            "branch_id": None,
            "step_ids": [],
        }
    )
    run.node_executions[local_execution.id] = local_execution
    local_execution._workflow_execution = run
    message_id = None
    if message is not None:
        history.upsert_message(run, message)
        message_id = message.id
    return history.upsert_step(
        run,
        state.Step(
            id=step_id or uuid4(),
            execution_id=local_execution.id,
            type=step_type,
            message_id=message_id,
            output_mode=output_mode,
            is_final=is_final,
        ),
    )


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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    step_id = uuid4()
    step1 = _make_step(
        execution,
        step_id=step_id,
        step_type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.ASSISTANT, text="first"),
    )

    ui_state.handle_step(step1)

    components = terminal.components
    assert len(components) == 4
    header = components[0]
    step_component = components[1]
    input_component = components[2]

    assert header is not step_component
    assert input_component is not step_component
    assert isinstance(step_component, tui_step_output_component.StepOutputComponent)
    assert step_component.content_type is models.StepContentType.MARKDOWN
    assert step_component.text == "first"

    step2 = _make_step(
        execution,
        step_id=step_id,
        step_type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.ASSISTANT, text="second\n\n"),
    )

    ui_state.handle_step(step2)

    components_after_update = terminal.components
    assert len(components_after_update) == 4
    assert components_after_update[1] is step_component
    assert step_component.text == "second"

    step_no_message = _make_step(
        execution,
        step_type=state.StepType.OUTPUT_MESSAGE,
        message=None,
    )
    ui_state.handle_step(step_no_message)
    assert len(terminal.components) == 4

    step_empty_text = _make_step(
        execution,
        step_id=step_id,
        step_type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.ASSISTANT, text="\n\n"),
    )

    ui_state.handle_step(step_empty_text)
    assert len(terminal.components) == 4
    assert step_component.text == ""

    await terminal.render()
    output = buffer.getvalue()
    assert "second" not in output
    assert "first" not in output


@pytest.mark.asyncio
async def test_tui_state_uses_markdown_render_mode_setting() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    tui_opts = vocode_settings.TUIOptions(
        markdown_render_mode=vocode_settings.MarkdownRenderMode.syntax,
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

    header = ui_state.terminal.components[0]
    assert isinstance(header, tui_renderable_component.CallbackComponent)

    ui_state.add_markdown("hello")
    markdown_component = ui_state.terminal.components[1]
    assert isinstance(markdown_component, tui_markdown_component.MarkdownComponent)
    assert (
        markdown_component.render_mode
        is tui_markdown_component.MarkdownRenderMode.SYNTAX
    )


@pytest.mark.asyncio
async def test_tui_state_banner_uses_pyfiglet_and_is_centered() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(
        file=buffer,
        force_terminal=True,
        color_system=None,
        width=80,
        height=200,
    )

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    tui_opts = vocode_settings.TUIOptions(
        banner_text="VOCODE",
        banner_font="chunky",
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

    header = ui_state.terminal.components[0]
    assert isinstance(header, tui_renderable_component.CallbackComponent)

    lines = header.render(console.options)
    output: list[str] = []
    for line in lines:
        output.append("".join([seg.text for seg in line]))

    fig = pyfiglet.Figlet(font="chunky")
    expected_first_line = fig.renderText("VOCODE").splitlines()[0].rstrip("\n")

    matching_line = None
    for line in output:
        if expected_first_line.strip() in line:
            matching_line = line
            break
    assert matching_line is not None

    left_padding = len(matching_line) - len(matching_line.lstrip(" "))
    assert left_padding > 0


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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.OUTPUT_MESSAGE,
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    step_id = uuid4()
    step1 = _make_step(
        execution,
        step_id=step_id,
        step_type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.ASSISTANT, text="interim"),
        output_mode=models.OutputMode.HIDE_FINAL,
    )

    ui_state.handle_step(step1)

    components = terminal.components
    assert len(components) == 4
    step_component = components[1]
    assert isinstance(step_component, tui_step_output_component.StepOutputComponent)
    assert step_component.text == "interim"

    step2 = _make_step(
        execution,
        step_id=step_id,
        step_type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.ASSISTANT, text="final"),
        output_mode=models.OutputMode.HIDE_FINAL,
        is_final=True,
    )

    ui_state.handle_step(step2)

    components_after_update = terminal.components
    assert len(components_after_update) == 4
    assert components_after_update[1] is step_component
    assert step_component.text == "interim"

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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    rejection_step = _make_step(
        execution,
        step_type=state.StepType.REJECTION,
        message=state.Message(
            role=models.Role.USER,
            text="Rejected because of reasons.",
        ),
    )
    ui_state.handle_step(rejection_step)

    await ui_state.terminal.render()
    output = buffer.getvalue()
    assert "Rejected because of reasons." in output


@pytest.mark.asyncio
async def test_tui_state_renders_user_output_messages_with_input_style() -> None:
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    step = _make_step(
        execution,
        step_type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.USER, text="from-http"),
    )
    ui_state.handle_step(step)

    component = ui_state.terminal.components[1]
    assert isinstance(component, tui_rich_text_component.RichTextComponent)
    assert component.text == "> from-http"
    assert component.component_style == tui_styles.INPUT_MESSAGE_COMPONENT_STYLE


@pytest.mark.asyncio
async def test_tui_state_renders_user_rejection_steps_with_input_style() -> None:
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    step = _make_step(
        execution,
        step_type=state.StepType.REJECTION,
        message=state.Message(role=models.Role.USER, text="no thanks"),
    )
    ui_state.handle_step(step)

    component = ui_state.terminal.components[1]
    assert isinstance(component, tui_rich_text_component.RichTextComponent)
    assert component.text == "> no thanks"
    assert component.component_style == tui_styles.INPUT_MESSAGE_COMPONENT_STYLE


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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    req = state.ToolCallReq(
        id="call_1",
        name="tool",
        arguments={},
        status=state.ToolCallReqStatus.REQUIRES_CONFIRMATION,
    )
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
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
    assert input_component.cursor_row == len(input_component.lines) - 1


@pytest.mark.asyncio
async def test_tool_request_with_confirmation_collapsed_when_disabled() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    tui_opts = vocode_settings.TUIOptions(expand_confirm_tools=False)

    ui_state = tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
        tui_options=tui_opts,
    )

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    req = state.ToolCallReq(
        id="call_1",
        name="tool",
        arguments={},
        status=state.ToolCallReqStatus.REQUIRES_CONFIRMATION,
    )
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    req = state.ToolCallReq(
        id="call_1",
        name="update_plan",
        arguments={},
        status=state.ToolCallReqStatus.PENDING_EXECUTION,
    )
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
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
async def test_tool_request_run_agent_uses_formatter_default_for_stats() -> None:
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    req = state.ToolCallReq(
        id="call_1",
        name="run_agent",
        arguments={"name": "agent1"},
        status=state.ToolCallReqStatus.EXECUTING,
    )
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
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
    tool_component = tool_components[0]
    assert tool_component.show_execution_stats is False

    await ui_state.terminal.render()
    assert tool_component not in ui_state.terminal._animation_components


@pytest.mark.asyncio
async def test_tool_request_apply_patch_uses_formatter_default_for_stats() -> None:
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    req = state.ToolCallReq(
        id="call_1",
        name="apply_patch",
        arguments={"text": "*** Begin Patch\n*** End Patch"},
        status=state.ToolCallReqStatus.EXECUTING,
    )
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
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
    tool_component = tool_components[0]
    assert tool_component.show_execution_stats is False

    await ui_state.terminal.render()
    assert tool_component not in ui_state.terminal._animation_components


@pytest.mark.asyncio
async def test_tool_request_run_agent_renders_prompt_text() -> None:
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    req = state.ToolCallReq(
        id="call_1",
        name="run_agent",
        arguments={"name": "agent1", "text": "hello world"},
        status=state.ToolCallReqStatus.EXECUTING,
    )
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[req],
        ),
    )

    ui_state.handle_step(step)
    await ui_state.terminal.render()
    output = buffer.getvalue()
    assert "name=agent1" in output
    assert "text=hello world" in output


@pytest.mark.asyncio
async def test_tool_request_run_agent_has_static_indicator_without_status_text() -> (
    None
):
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    created_at = datetime.datetime.now(datetime.UTC)
    handled_at = created_at + datetime.timedelta(seconds=5)
    req = state.ToolCallReq(
        id="call_1",
        name="run_agent",
        arguments={"name": "agent1", "text": "hello world"},
        status=state.ToolCallReqStatus.EXECUTING,
        created_at=created_at,
        handled_at=handled_at,
    )
    resp = state.ToolCallResp(
        id="call_1",
        name="run_agent",
        status=state.ToolCallStatus.COMPLETED,
        result={"ok": True},
    )
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[req],
            tool_call_responses=[resp],
        ),
    )

    ui_state.handle_step(step)
    await ui_state.terminal.render()

    output = buffer.getvalue()
    assert "name=agent1" in output
    assert "text=hello world" in output
    assert "running" not in output
    assert "done" not in output
    assert "5s" not in output


@pytest.mark.asyncio
async def test_tool_request_components_do_not_insert_blank_line_between_tools() -> None:
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    step1 = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[
                state.ToolCallReq(
                    id="call_1",
                    name="list_files",
                    arguments={"pattern": "*"},
                    status=state.ToolCallReqStatus.EXECUTING,
                )
            ],
        ),
    )
    step2 = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[
                state.ToolCallReq(
                    id="call_2",
                    name="list_files",
                    arguments={"pattern": "**/*"},
                    status=state.ToolCallReqStatus.EXECUTING,
                )
            ],
        ),
    )

    ui_state.handle_step(step1)
    ui_state.handle_step(step2)

    await ui_state.terminal.render()
    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    list_lines = [line for line in lines if "List Files" in line]

    assert len(list_lines) == 2


@pytest.mark.asyncio
async def test_tool_request_uses_response_completion_for_inline_status() -> None:
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    created_at = datetime.datetime.now(datetime.UTC)
    handled_at = created_at + datetime.timedelta(milliseconds=200)
    req = state.ToolCallReq(
        id="call_1",
        name="list_files",
        arguments={"pattern": "**/*"},
        status=state.ToolCallReqStatus.EXECUTING,
        created_at=created_at,
        handled_at=handled_at,
    )
    resp = state.ToolCallResp(
        id="call_1",
        name="list_files",
        status=state.ToolCallStatus.COMPLETED,
        result={"items": []},
    )
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[req],
            tool_call_responses=[resp],
        ),
    )

    ui_state.handle_step(step)

    await ui_state.terminal.render()
    output = buffer.getvalue()
    assert "done" in output
    assert "running" not in output


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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    req = state.ToolCallReq(
        id="call_1",
        name="tool",
        arguments={},
        status=state.ToolCallReqStatus.REQUIRES_CONFIRMATION,
    )
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
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


@pytest.mark.asyncio
async def test_tool_request_pending_renders_clock_icon() -> None:
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    req = state.ToolCallReq(
        id="call_1",
        name="list_files",
        arguments={"pattern": "*"},
        status=state.ToolCallReqStatus.PENDING_EXECUTION,
    )
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[req],
        ),
    )

    ui_state.handle_step(step)
    await ui_state.terminal.render()

    output = buffer.getvalue()
    assert "⏳" in output
    assert "[pending]" in output


@pytest.mark.asyncio
async def test_tool_request_completed_renders_checkmark_icon_for_autoapproved_tool() -> (
    None
):
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

    execution = state.NodeExecution(node="node", status=state.RunStatus.RUNNING)
    req = state.ToolCallReq(
        id="call_1",
        name="list_files",
        arguments={"pattern": "*"},
        status=state.ToolCallReqStatus.PENDING_EXECUTION,
        auto_approved=True,
    )
    resp = state.ToolCallResp(
        id="call_1",
        name="list_files",
        status=state.ToolCallStatus.COMPLETED,
        result={"items": []},
    )
    step = _make_step(
        execution,
        step_id=uuid4(),
        step_type=state.StepType.TOOL_REQUEST,
        message=state.Message(
            role=models.Role.ASSISTANT,
            text="",
            tool_call_requests=[req],
            tool_call_responses=[resp],
        ),
    )

    ui_state.handle_step(step)
    await ui_state.terminal.render()

    output = buffer.getvalue()
    assert "✔︎" in output or "✔" in output
    assert "done" in output
    assert "✖" not in output
