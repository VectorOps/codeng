from __future__ import annotations

import asyncio
import io

import pytest
from rich import console as rich_console

from vocode import models
from vocode import settings as vocode_settings
from vocode.manager.helpers import InMemoryEndpoint
from vocode.manager import proto as manager_proto
from vocode.manager.server import UIServer
from vocode.tui import uistate as tui_uistate
from vocode.tui.lib.components import markdown_component as tui_markdown_component
from vocode.tui.lib.input import base as input_base
from tests.stub_project import StubProject


@pytest.mark.asyncio
async def test_runner_req_display_opts_applied_to_markdown_component() -> None:
    settings = vocode_settings.Settings()
    settings.workflows["wf"] = vocode_settings.WorkflowConfig(
        need_input=False,
        nodes=[
            {
                "name": "n1",
                "type": "noop",
                "outcomes": [{"name": "done"}],
                "collapse": True,
                "collapse_lines": 2,
            },
            {
                "name": "end",
                "type": "noop",
                "outcomes": [],
            },
        ],
        edges=[
            {
                "source_node": "n1",
                "source_outcome": "done",
                "target_node": "end",
            },
        ],
    )
    project = StubProject(settings=settings)

    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)
    await server.start()
    await server.manager.start_workflow("wf")

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

    while True:
        envelope = await asyncio.wait_for(client_endpoint.recv(), timeout=1.0)
        if envelope.payload.kind == manager_proto.BasePacketKind.RUNNER_REQ:
            break
    payload = envelope.payload
    assert payload.display is not None
    assert payload.display.collapse is True
    assert payload.display.collapse_lines == 2

    ui_state.handle_step(payload.step, display=payload.display)
    terminal = ui_state.terminal
    components = terminal.components
    assert len(components) == 4
    step_component = components[1]
    assert isinstance(step_component, tui_markdown_component.MarkdownComponent)
    assert step_component.is_collapsed is True
    assert step_component.compact_lines == 2

    await server.stop()
