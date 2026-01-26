from __future__ import annotations

import asyncio
import logging

import pytest

from vocode import models, state
from typing import Optional

from vocode import settings as vocode_settings
from vocode.manager.base import BaseManager, RunnerFrame
from vocode.manager.helpers import InMemoryEndpoint
from vocode.manager.server import UIServer
from vocode.manager import proto as manager_proto
from vocode.runner import proto as runner_proto
from tests.stub_project import StubProject
from vocode.manager import autocomplete_providers as autocomplete_providers
from tests.manager.runner_stubs import DummyRunnerWithWorkflow


@pytest.mark.asyncio
async def test_uiserver_applies_logging_settings() -> None:
    project_settings = vocode_settings.Settings()
    project_settings.logging = vocode_settings.LoggingSettings(
        default_level=vocode_settings.LogLevel.error,
        enabled_loggers={"custom.logger": vocode_settings.LogLevel.debug},
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
async def test_uiserver_on_runner_event_roundtrip() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    node_execution = state.NodeExecution(
        node="node1",
        status=state.RunStatus.RUNNING,
    )
    execution = state.WorkflowExecution(workflow_name="wf-ui-server")
    execution.node_executions[node_execution.id] = node_execution
    step = state.Step(
        execution=node_execution,
        type=state.StepType.OUTPUT_MESSAGE,
    )
    execution.steps.append(step)
    event = runner_proto.RunEventReq(
        kind=runner_proto.RunEventReqKind.STEP,
        execution=execution,
        step=step,
    )
    async def dummy_coro() -> None:
        await asyncio.Event().wait()

    dummy_task = asyncio.create_task(dummy_coro())
    runner = DummyRunnerWithWorkflow(["node1"])
    frame = RunnerFrame(
        workflow_name="wf-ui-server",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )

    assert server.manager.project is project

    response_task = asyncio.create_task(server.on_runner_event(frame, event))

    request_envelope = await client_endpoint.recv()
    assert request_envelope.payload.kind == manager_proto.BasePacketKind.RUNNER_REQ
    req_payload = request_envelope.payload
    assert isinstance(req_payload, manager_proto.RunnerReqPacket)
    assert req_payload.workflow_id == frame.workflow_name
    assert req_payload.workflow_name == execution.workflow_name
    assert req_payload.workflow_execution_id == str(execution.id)
    assert req_payload.step == step
    assert req_payload.input_required is False

    resp = await response_task
    assert resp is not None
    assert resp.resp_type == runner_proto.RunEventResponseType.NOOP
    assert resp.message is None

    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task


@pytest.mark.asyncio
async def test_uiserver_clears_input_waiters_on_runner_stop() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)
    await server.start()

    execution = state.WorkflowExecution(workflow_name="wf-ui-stop-input")
    stats = runner_proto.RunStats(
        status=state.RunnerStatus.STOPPED,
        current_node_name="node-stop",
    )

    class DummyRunner:
        def __init__(self, execution: state.WorkflowExecution) -> None:
            self.execution = execution

    async def dummy_coro() -> None:
        await asyncio.Event().wait()

    dummy_task = asyncio.create_task(dummy_coro())
    runner = DummyRunner(execution)
    frame = RunnerFrame(
        workflow_name=execution.workflow_name,
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
        last_stats=stats,
    )
    server.manager._runner_stack.append(frame)

    # Simulate that a prompt was shown and an input waiter is pending.
    server._push_input_waiter()

    event = runner_proto.RunEventReq(
        kind=runner_proto.RunEventReqKind.STATUS,
        execution=execution,
        stats=stats,
    )

    await server.on_runner_event(frame, event)

    # First packet is an INPUT_PROMPT clearing the prompt, followed by UI_STATE.
    envelope_prompt = await client_endpoint.recv()
    prompt_payload = envelope_prompt.payload
    assert prompt_payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(prompt_payload, manager_proto.InputPromptPacket)
    assert prompt_payload.title is None
    assert prompt_payload.subtitle is None

    envelope_state = await client_endpoint.recv()
    assert envelope_state.payload.kind == manager_proto.BasePacketKind.UI_STATE

    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task


@pytest.mark.asyncio
async def test_uiserver_handles_autocomplete_request() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    req = manager_proto.AutocompleteReqPacket(text="he", row=0, col=2)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=req)
    await client_endpoint.send(envelope)

    server_incoming = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_incoming)
    assert handled is True

    resp_envelope = await client_endpoint.recv()
    resp_payload = resp_envelope.payload
    assert resp_payload.kind == manager_proto.BasePacketKind.AUTOCOMPLETE_RESP
    assert isinstance(resp_payload, manager_proto.AutocompleteRespPacket)
    assert resp_payload.items == []


@pytest.mark.asyncio
async def test_run_autocomplete_provider_uses_workflow_name_values() -> None:
    project = StubProject()
    workflow_name = "wf-auto"
    project.settings.workflows[workflow_name] = vocode_settings.WorkflowConfig()
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    items = await autocomplete_providers.run_autocomplete_provider(
        server,
        "/run ",
        0,
        5,
    )

    assert items is not None
    assert [item.title for item in items] == ["/run wf-auto - workflow"]
    assert [item.replace_start for item in items] == [0]
    assert [item.replace_text for item in items] == ["/run "]
    assert [item.insert_text for item in items] == [f"/run {workflow_name}"]


@pytest.mark.asyncio
async def test_run_autocomplete_provider_does_not_suggest_exact_match() -> None:
    project = StubProject()
    workflow_name = "wf-auto"
    project.settings.workflows[workflow_name] = vocode_settings.WorkflowConfig()
    server_endpoint, _ = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    items = await autocomplete_providers.run_autocomplete_provider(
        server,
        "/run wf-auto",
        0,
        len("/run wf-auto"),
    )
    assert items is not None
    assert [item.title for item in items] == ["/run wf-auto - workflow"]
    assert [item.replace_start for item in items] == [0]
    assert [item.replace_text for item in items] == ["/run wf-auto"]
    assert [item.insert_text for item in items] == ["/run wf-auto"]


@pytest.mark.asyncio
async def test_uiserver_on_runner_event_user_input_message() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    node_execution = state.NodeExecution(
        node="node1",
        status=state.RunStatus.RUNNING,
    )
    execution = state.WorkflowExecution(workflow_name="wf-ui-server-user-input")
    execution.node_executions[node_execution.id] = node_execution
    step = state.Step(
        execution=node_execution,
        type=state.StepType.PROMPT,
    )
    execution.steps.append(step)
    event = runner_proto.RunEventReq(
        kind=runner_proto.RunEventReqKind.STEP,
        execution=execution,
        step=step,
    )
    async def dummy_coro() -> None:
        await asyncio.Event().wait()

    dummy_task = asyncio.create_task(dummy_coro())
    runner = DummyRunnerWithWorkflow(["node1"])
    frame = RunnerFrame(
        workflow_name="wf-ui-server-user-input",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )

    assert server.manager.project is project

    response_task = asyncio.create_task(server.on_runner_event(frame, event))

    request_envelope = await client_endpoint.recv()
    assert request_envelope.payload.kind == manager_proto.BasePacketKind.RUNNER_REQ
    req_payload = request_envelope.payload
    assert isinstance(req_payload, manager_proto.RunnerReqPacket)
    assert req_payload.workflow_id == frame.workflow_name
    assert req_payload.workflow_name == execution.workflow_name
    assert req_payload.workflow_execution_id == str(execution.id)
    assert req_payload.step == step
    initial_prompt_envelope = await client_endpoint.recv()
    initial_prompt_payload = initial_prompt_envelope.payload
    assert initial_prompt_payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(initial_prompt_payload, manager_proto.InputPromptPacket)
    user_message = state.Message(
        role=models.Role.USER,
        text="user input message",
    )
    user_input_packet = manager_proto.UserInputPacket(
        message=user_message,
    )
    response_envelope = manager_proto.BasePacketEnvelope(
        msg_id=request_envelope.msg_id + 1,
        payload=user_input_packet,
    )
    await client_endpoint.send(response_envelope)

    server_incoming = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_incoming)
    assert handled is True

    resp = await response_task
    assert resp is not None
    assert resp.resp_type == runner_proto.RunEventResponseType.MESSAGE
    assert resp.message == user_message

    prompt_envelope = await client_endpoint.recv()
    prompt_payload = prompt_envelope.payload
    assert prompt_payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(prompt_payload, manager_proto.InputPromptPacket)
    # After user input, UIServer clears the prompt.
    assert prompt_payload.title is None
    assert prompt_payload.subtitle is None

    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task


@pytest.mark.asyncio
async def test_uiserver_on_runner_event_user_input_prompt_confirm_title() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    node_execution = state.NodeExecution(
        node="node1",
        status=state.RunStatus.RUNNING,
    )
    execution = state.WorkflowExecution(workflow_name="wf-ui-server-user-input-confirm")
    execution.node_executions[node_execution.id] = node_execution
    step = state.Step(
        execution=node_execution,
        type=state.StepType.PROMPT_CONFIRM,
    )
    execution.steps.append(step)
    event = runner_proto.RunEventReq(
        kind=runner_proto.RunEventReqKind.STEP,
        execution=execution,
        step=step,
    )
    async def dummy_coro() -> None:
        await asyncio.Event().wait()

    dummy_task = asyncio.create_task(dummy_coro())
    runner = DummyRunnerWithWorkflow(["node1"])
    frame = RunnerFrame(
        workflow_name="wf-ui-server-user-input-confirm",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )

    response_task = asyncio.create_task(server.on_runner_event(frame, event))

    _ = await client_endpoint.recv()
    prompt_envelope = await client_endpoint.recv()
    prompt_payload = prompt_envelope.payload
    assert prompt_payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(prompt_payload, manager_proto.InputPromptPacket)
    assert prompt_payload.title == "Press enter to confirm or provide a reply"

    user_message = state.Message(
        role=models.Role.USER,
        text="",
    )
    user_input_packet = manager_proto.UserInputPacket(
        message=user_message,
    )
    response_envelope = manager_proto.BasePacketEnvelope(
        msg_id=prompt_envelope.msg_id + 1,
        payload=user_input_packet,
    )
    await client_endpoint.send(response_envelope)

    server_incoming = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_incoming)
    assert handled is True

    resp = await response_task
    assert resp is not None
    assert resp.resp_type == runner_proto.RunEventResponseType.APPROVE

    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task


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
async def test_uiserver_status_event_emits_ui_state_packet() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)
    await server.start()
    execution = state.WorkflowExecution(workflow_name="wf-ui-status")

    execution.llm_usage = state.LLMUsageStats(
        prompt_tokens=123,
        completion_tokens=456,
        cost_dollars=1.23,
    )
    execution.last_step_llm_usage = state.LLMUsageStats(
        prompt_tokens=10,
        completion_tokens=5,
        cost_dollars=0.01,
        input_token_limit=1000,
    )

    stats = runner_proto.RunStats(
        status=state.RunnerStatus.RUNNING,
        current_node_name="node-status",
    )

    class DummyRunner:
        def __init__(self, execution: state.WorkflowExecution) -> None:
            self.execution = execution

    async def dummy_coro() -> None:
        await asyncio.Event().wait()

    dummy_task = asyncio.create_task(dummy_coro())
    runner = DummyRunner(execution)
    frame = RunnerFrame(
        workflow_name=execution.workflow_name,
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
        last_stats=stats,
    )
    server.manager._runner_stack.append(frame)

    event = runner_proto.RunEventReq(
        kind=runner_proto.RunEventReqKind.STATUS,
        execution=execution,
        stats=stats,
    )

    assert server.manager.project is project
    resp = await server.on_runner_event(frame, event)
    assert resp is not None
    assert resp.resp_type == runner_proto.RunEventResponseType.NOOP
    assert resp.message is None

    envelope = await client_endpoint.recv()
    payload = envelope.payload
    assert payload.kind == manager_proto.BasePacketKind.UI_STATE
    assert isinstance(payload, manager_proto.UIServerStatePacket)

    state_packet = payload
    assert state_packet.status == manager_proto.UIServerStatus.RUNNING
    assert len(state_packet.runners) == 1
    runner_state = state_packet.runners[0]
    assert runner_state.workflow_name == execution.workflow_name
    assert runner_state.workflow_execution_id == str(execution.id)
    assert runner_state.node_name == stats.current_node_name
    assert runner_state.status == stats.status
    assert state_packet.last_step_llm_usage is not None
    assert state_packet.last_step_llm_usage.prompt_tokens == 10
    assert state_packet.last_step_llm_usage.completion_tokens == 5
    assert state_packet.last_step_llm_usage.input_token_limit == 1000

    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task


@pytest.mark.asyncio
async def test_uiserver_handles_stop_request_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    called: list[object] = []

    async def fake_stop_current_runner() -> None:
        called.append(object())

    monkeypatch.setattr(server.manager, "stop_current_runner", fake_stop_current_runner)

    stop_packet = manager_proto.StopReqPacket()
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=stop_packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)

    assert handled is True
    assert called


@pytest.mark.asyncio
async def test_uiserver_user_input_triggers_history_edit_when_no_waiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    class DummyRunner:
        def __init__(self) -> None:
            self.status = state.RunnerStatus.STOPPED

    runner = DummyRunner()
    frame = RunnerFrame(
        workflow_name="wf-user-input-edit",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )
    server.manager._runner_stack.append(frame)

    called: list[str] = []

    async def fake_edit_history_with_text(
        self: BaseManager,
        text: str,
    ) -> bool:
        called.append(text)
        return True

    monkeypatch.setattr(
        BaseManager, "edit_history_with_text", fake_edit_history_with_text
    )

    user_message = state.Message(
        role=models.Role.USER,
        text="new input text",
    )
    packet = manager_proto.UserInputPacket(message=user_message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=1, payload=packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)

    assert handled is True
    assert called == ["new input text"]


@pytest.mark.asyncio
async def test_uiserver_user_input_sends_error_when_edit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    class DummyRunner:
        def __init__(self) -> None:
            self.status = state.RunnerStatus.STOPPED

    runner = DummyRunner()
    frame = RunnerFrame(
        workflow_name="wf-user-input-edit-fail",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )
    server.manager._runner_stack.append(frame)

    async def fake_edit_history_with_text(
        self: BaseManager,
        text: str,
    ) -> bool:
        return False

    monkeypatch.setattr(
        BaseManager, "edit_history_with_text", fake_edit_history_with_text
    )

    messages: list[str] = []

    async def fake_send_text_message(
        text: str,
        text_format: manager_proto.TextMessageFormat = manager_proto.TextMessageFormat.PLAIN,
    ) -> None:
        _ = text_format
        messages.append(text)

    monkeypatch.setattr(server, "send_text_message", fake_send_text_message)

    user_message = state.Message(
        role=models.Role.USER,
        text="new input text",
    )
    packet = manager_proto.UserInputPacket(message=user_message)
    envelope = manager_proto.BasePacketEnvelope(msg_id=2, payload=packet)
    await client_endpoint.send(envelope)

    server_envelope = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_envelope)

    assert handled is True
    assert messages
    assert "Unable to edit history" in messages[0]
