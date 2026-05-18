from __future__ import annotations

import asyncio
import logging
from typing import Optional

import pytest

from tests.stub_project import StubProject
from vocode import models, state
from vocode import ui_events
from vocode.input_manager import INPUT_TYPE_INTERACTIVE
from vocode import settings as vocode_settings
from vocode.manager import proto as manager_proto
from vocode.manager.base import BaseManager
from vocode.manager.helpers import InMemoryEndpoint
from vocode.manager.server import UIServer


@pytest.mark.asyncio
async def test_uiserver_applies_logging_settings() -> None:
    project_settings = vocode_settings.Settings()
    project_settings.logging.default_level = vocode_settings.LogLevel.error
    project_settings.logging.enabled_loggers["custom.logger"] = (
        vocode_settings.LogLevel.debug
    )

    project = StubProject(settings=project_settings)
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    root_logger = logging.getLogger()
    vocode_logger = logging.getLogger("vocode")
    custom_logger = logging.getLogger("custom.logger")

    orig_root_level = root_logger.level
    orig_vocode_level = vocode_logger.level
    orig_custom_level = custom_logger.level

    try:
        await server.start()

        assert root_logger.level == logging.ERROR
        assert vocode_logger.level == logging.ERROR
        assert custom_logger.level == logging.DEBUG
    finally:
        root_logger.setLevel(orig_root_level)
        vocode_logger.setLevel(orig_vocode_level)
        custom_logger.setLevel(orig_custom_level)


@pytest.mark.asyncio
async def test_uiserver_request_text_input_opens_and_clears_prompt() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    request_task = asyncio.create_task(
        server.request_text_input(title="Need input", subtitle="Type something")
    )

    prompt_envelope = await client_endpoint.recv()
    assert isinstance(prompt_envelope.payload, manager_proto.InputPromptPacket)
    assert prompt_envelope.payload.title == "Need input"
    assert prompt_envelope.payload.subtitle == "Type something"

    accepted = await project.input_manager.publish(
        state.Message(role=models.Role.USER, text="hello world"),
        queue=False,
        input_type=INPUT_TYPE_INTERACTIVE,
    )
    assert accepted is True

    result = await request_task
    assert result == "hello world"

    clear_envelope = await client_endpoint.recv()
    assert isinstance(clear_envelope.payload, manager_proto.InputPromptPacket)
    assert clear_envelope.payload.title is None
    assert clear_envelope.payload.subtitle is None


@pytest.mark.asyncio
async def test_uiserver_request_text_input_clears_prompt_on_cancel() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    request_task = asyncio.create_task(
        server.request_text_input(title="Need input", subtitle="Type something")
    )

    prompt_envelope = await client_endpoint.recv()
    assert isinstance(prompt_envelope.payload, manager_proto.InputPromptPacket)
    assert prompt_envelope.payload.title == "Need input"

    request_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_task

    clear_envelope = await client_endpoint.recv()
    assert isinstance(clear_envelope.payload, manager_proto.InputPromptPacket)
    assert clear_envelope.payload.title is None
    assert clear_envelope.payload.subtitle is None


@pytest.mark.asyncio
async def test_uiserver_autostarts_default_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = StubProject()
    workflow_name = "wf-auto-start"
    project.settings.workflows[workflow_name] = vocode_settings.WorkflowConfig()
    project.settings.default_workflow = workflow_name

    start_calls: list[tuple[str, Optional[state.Message]]] = []
    started = asyncio.Event()

    async def fake_start_workflow(
        self: BaseManager,
        wf_name: str,
        initial_message: Optional[state.Message] = None,
    ) -> object:
        start_calls.append((wf_name, initial_message))
        started.set()
        return object()

    monkeypatch.setattr(BaseManager, "start_workflow", fake_start_workflow)

    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await server.start()
    await asyncio.wait_for(started.wait(), timeout=1.0)

    assert start_calls == [(workflow_name, None)]


@pytest.mark.asyncio
async def test_uiserver_autostart_reports_workflow_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = StubProject()
    workflow_name = "wf-broken-auto-start"
    project.settings.workflows[workflow_name] = vocode_settings.WorkflowConfig()
    project.settings.default_workflow = workflow_name

    async def fake_start_workflow(
        self: BaseManager,
        wf_name: str,
        initial_message: Optional[state.Message] = None,
    ) -> object:
        _ = self
        _ = initial_message
        raise ValueError(f"workflow '{wf_name}' has invalid edges")

    monkeypatch.setattr(BaseManager, "start_workflow", fake_start_workflow)

    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await server.start()

    resp_envelope = await client_endpoint.recv()
    assert isinstance(resp_envelope.payload, manager_proto.UIEventPacket)
    assert resp_envelope.payload.event.title == "Workflow validation failed"
    assert resp_envelope.payload.event.source == workflow_name
    assert "could not start" in resp_envelope.payload.event.message


@pytest.mark.asyncio
async def test_uiserver_emits_ui_event_packet_for_project_event() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    await server.start()

    await project.publish_ui_event(
        ui_events.ProjectUIEvent(
            severity=ui_events.UIEventSeverity.ERROR,
            title="MCP source start failed",
            source="broken",
            message="MCP source 'broken' failed to start: boom",
        )
    )

    resp_envelope = await client_endpoint.recv()
    assert isinstance(resp_envelope.payload, manager_proto.UIEventPacket)
    assert resp_envelope.payload.event.title == "MCP source start failed"
    assert resp_envelope.payload.event.source == "broken"
    assert (
        resp_envelope.payload.event.message
        == "MCP source 'broken' failed to start: boom"
    )
