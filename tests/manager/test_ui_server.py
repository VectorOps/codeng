from __future__ import annotations

import asyncio

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

    class DummyRunner:
        pass

    async def dummy_coro() -> None:
        await asyncio.Event().wait()

    dummy_task = asyncio.create_task(dummy_coro())
    frame = RunnerFrame(
        workflow_name="wf-ui-server",
        runner=DummyRunner(),
        initial_message=None,
        task=dummy_task,
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
        task=dummy_task,
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

    req = manager_proto.AutocompleteReqPacket(text="he", cursor=2)
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

    class DummyRunner:
        pass

    async def dummy_coro() -> None:
        await asyncio.Event().wait()

    dummy_task = asyncio.create_task(dummy_coro())
    frame = RunnerFrame(
        workflow_name="wf-ui-server-user-input",
        runner=DummyRunner(),
        initial_message=None,
        task=dummy_task,
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

    class DummyRunner:
        pass

    async def dummy_coro() -> None:
        await asyncio.Event().wait()

    dummy_task = asyncio.create_task(dummy_coro())
    frame = RunnerFrame(
        workflow_name="wf-ui-server-user-input-confirm",
        runner=DummyRunner(),
        initial_message=None,
        task=dummy_task,
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
        task=dummy_task,
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
