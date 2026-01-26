from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from rich import console as rich_console

from vocode import state
from vocode.manager import proto as manager_proto
from vocode.tui import app as tui_app
from vocode.tui import uistate as tui_uistate
from vocode.tui.lib.input import base as input_base
from tests.stub_project import StubProject


def _make_tui_state_with_console() -> tui_uistate.TUIState:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)

    async def on_input(_: str) -> None:
        return None

    class DummyInputHandler(input_base.InputHandler):
        async def run(self) -> None:
            return None

    return tui_uistate.TUIState(
        on_input=on_input,
        console=console,
        input_handler=DummyInputHandler(),
        on_autocomplete_request=None,
        on_stop=None,
        on_eof=None,
    )


def test_tui_state_updates_toolbar_from_ui_state() -> None:
    ui_state = _make_tui_state_with_console()
    terminal = ui_state.terminal
    assert len(terminal.components) == 3
    toolbar = terminal.components[-1]

    execution = state.WorkflowExecution(workflow_name="wf-toolbar")
    stats = state.RunnerStatus.RUNNING

    runner_frame = manager_proto.RunnerStackFrame(
        workflow_name=execution.workflow_name,
        workflow_execution_id=str(execution.id),
        node_name="node-toolbar",
        status=stats,
    )
    packet = manager_proto.UIServerStatePacket(
        status=manager_proto.UIServerStatus.RUNNING,
        runners=[runner_frame],
    )

    ui_state.handle_ui_state(packet)

    assert toolbar.text == "wf-toolbar@node-toolbar"
    renderable = toolbar._build_renderable(rich_console.Console())
    rendered_text = str(renderable)
    assert "wf-toolbar@node-toolbar" in rendered_text
    assert "running" in rendered_text


@pytest.mark.asyncio
async def test_tui_app_handles_ui_state_packet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeTUIState:
        def __init__(
            self,
            on_input,
            on_autocomplete_request=None,
            on_stop=None,
            on_eof=None,
        ) -> None:
            self._on_input = on_input
            self.last_ui_state: manager_proto.UIServerStatePacket | None = None

        def add_markdown(self, markdown: str) -> None:
            return None

        def set_input_panel_title(
            self,
            title: str | None,
            subtitle: str | None = None,
        ) -> None:
            return None

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        def handle_ui_state(self, packet: manager_proto.UIServerStatePacket) -> None:
            self.last_ui_state = packet

    monkeypatch.setattr(tui_uistate, "TUIState", FakeTUIState)

    monkeypatch.setattr(
        "vocode.project.Project.from_base_path",
        lambda path: StubProject(),
    )

    app = tui_app.App(project_path=tmp_path)
    state_obj = app._state  # FakeTUIState

    execution = state.WorkflowExecution(workflow_name="wf-app-ui-state")
    runner_frame = manager_proto.RunnerStackFrame(
        workflow_name=execution.workflow_name,
        workflow_execution_id=str(execution.id),
        node_name="node-app",
        status=state.RunnerStatus.RUNNING,
    )
    packet = manager_proto.UIServerStatePacket(
        status=manager_proto.UIServerStatus.RUNNING,
        runners=[runner_frame],
    )
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=packet)

    await app._handle_packet_ui_state(envelope)

    assert state_obj.last_ui_state is packet


def test_toolbar_shows_stacked_runners_and_usage() -> None:
    ui_state = _make_tui_state_with_console()
    terminal = ui_state.terminal
    toolbar = terminal.components[-1]

    execution1 = state.WorkflowExecution(workflow_name="wf1")
    execution2 = state.WorkflowExecution(workflow_name="wf2")

    runner_frame1 = manager_proto.RunnerStackFrame(
        workflow_name=execution1.workflow_name,
        workflow_execution_id=str(execution1.id),
        node_name="node1",
        status=state.RunnerStatus.RUNNING,
    )
    runner_frame2 = manager_proto.RunnerStackFrame(
        workflow_name=execution2.workflow_name,
        workflow_execution_id=str(execution2.id),
        node_name="node2",
        status=state.RunnerStatus.RUNNING,
    )

    active_usage = state.LLMUsageStats(
        prompt_tokens=10,
        completion_tokens=5,
        cost_dollars=0.01,
        input_token_limit=1000,
    )
    project_usage = state.LLMUsageStats(
        prompt_tokens=100,
        completion_tokens=50,
        cost_dollars=0.25,
    )

    packet = manager_proto.UIServerStatePacket(
        status=manager_proto.UIServerStatus.RUNNING,
        runners=[runner_frame1, runner_frame2],
        active_workflow_llm_usage=active_usage,
        last_step_llm_usage=active_usage,
        project_llm_usage=project_usage,
    )

    ui_state.handle_ui_state(packet)

    assert toolbar.text == "wf1@node1 > wf2@node2"
    renderable = toolbar._build_renderable(rich_console.Console())
    rendered_text = str(renderable)
    assert "wf1@node1 > wf2@node2" in rendered_text
    assert "step: 10/1k" in rendered_text
    assert "ts: 100" in rendered_text
    assert "tr: 50" in rendered_text
    assert "$0.25" in rendered_text
