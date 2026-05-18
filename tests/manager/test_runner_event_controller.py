from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from tests.manager.runner_stubs import DummyRunnerWithWorkflow
from tests.stub_project import StubProject
from vocode import models, state
from vocode.history.manager import HistoryManager
from vocode.input_manager import INPUT_TYPE_INTERACTIVE
from vocode.manager import proto as manager_proto
from vocode.manager.base import BaseManager, RunnerFrame
from vocode.manager.helpers import InMemoryEndpoint
from vocode.manager.server import UIServer
from vocode.runner import proto as runner_proto
from vocode.runner.executors.llm.compaction import CompactionSummaryState


@pytest.mark.asyncio
async def test_uiserver_on_runner_event_roundtrip() -> None:
    history = HistoryManager()
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    execution = state.WorkflowExecution(workflow_name="wf-ui-server")
    node_execution = history.upsert_node_execution(
        execution,
        state.NodeExecution(
            node="node1",
            status=state.RunStatus.RUNNING,
        ),
    )
    step = history.upsert_step(
        execution,
        state.Step(
            execution_id=node_execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )
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
async def test_uiserver_on_runner_event_forwards_compaction_step() -> None:
    history = HistoryManager()
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    execution = state.WorkflowExecution(workflow_name="wf-ui-server-compaction")
    node_execution = history.upsert_node_execution(
        execution,
        state.NodeExecution(
            node="node1",
            status=state.RunStatus.RUNNING,
        ),
    )
    summary_message = state.Message(
        role=models.Role.SYSTEM,
        text="The conversation history before this point was compacted.",
    )
    history.upsert_message(execution, summary_message)
    step = history.upsert_step(
        execution,
        state.Step(
            execution_id=node_execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=summary_message.id,
            state=CompactionSummaryState(
                compacted_step_ids=[uuid4()],
                tokens_before=120,
                tokens_after_estimate=45,
                trigger_threshold_ratio=0.5,
            ),
            is_complete=True,
        ),
    )
    event = runner_proto.RunEventReq(
        kind=runner_proto.RunEventReqKind.STEP,
        execution=execution,
        step=step,
    )

    runner = DummyRunnerWithWorkflow(["node1"], execution=execution)
    frame = RunnerFrame(
        workflow_name="wf-ui-server-compaction",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )

    response_task = asyncio.create_task(server.on_runner_event(frame, event))

    request_envelope = await client_endpoint.recv()
    req_payload = request_envelope.payload
    assert isinstance(req_payload, manager_proto.RunnerReqPacket)
    assert req_payload.step.type == state.StepType.CONTEXT_COMPACTION
    assert req_payload.step.state is not None

    resp = await response_task
    assert resp is not None
    assert resp.resp_type == runner_proto.RunEventResponseType.NOOP


@pytest.mark.asyncio
async def test_uiserver_active_node_started_at_uses_first_step_time() -> None:
    history = HistoryManager()
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)
    await server.start()

    execution = state.WorkflowExecution(workflow_name="wf-ui-node-start-time")
    node_execution = history.upsert_node_execution(
        execution,
        state.NodeExecution(
            node="node-start",
            status=state.RunStatus.RUNNING,
        ),
    )

    stats = runner_proto.RunStats(
        status=state.RunnerStatus.RUNNING,
        current_node_name=node_execution.node,
        current_node_execution_id=node_execution.id,
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

    resp = await server.on_runner_event(frame, event)
    assert resp is not None

    envelope = await client_endpoint.recv()
    payload = envelope.payload
    assert isinstance(payload, manager_proto.UIServerStatePacket)
    assert payload.active_node_started_at is None

    message = state.Message(role=models.Role.USER, text="hello")
    history.upsert_message(execution, message)
    step = history.upsert_step(
        execution,
        state.Step(
            execution_id=node_execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=message.id,
        ),
    )

    resp2 = await server.on_runner_event(frame, event)
    assert resp2 is not None

    envelope2 = await client_endpoint.recv()
    payload2 = envelope2.payload
    assert isinstance(payload2, manager_proto.UIServerStatePacket)
    assert payload2.active_node_started_at == step.created_at
    assert payload2.last_user_input_at == execution.last_user_input_at

    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task


@pytest.mark.asyncio
async def test_uiserver_clears_input_waiters_on_runner_stop() -> None:
    history = HistoryManager()
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)
    await server.start()

    execution = state.WorkflowExecution(workflow_name="wf-ui-stop-input")
    node_execution = history.upsert_node_execution(
        execution,
        state.NodeExecution(
            node="node-stop",
            status=state.RunStatus.RUNNING,
        ),
    )
    stats = runner_proto.RunStats(
        status=state.RunnerStatus.STOPPED,
        current_node_name=node_execution.node,
        current_node_execution_id=node_execution.id,
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

    await server.on_runner_event(frame, event)

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
async def test_uiserver_on_runner_event_user_input_message() -> None:
    history = HistoryManager()
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    execution = state.WorkflowExecution(workflow_name="wf-ui-server-user-input")
    node_execution = history.upsert_node_execution(
        execution,
        state.NodeExecution(
            node="node1",
            status=state.RunStatus.RUNNING,
        ),
    )
    step = history.upsert_step(
        execution,
        state.Step(
            execution_id=node_execution.id,
            type=state.StepType.PROMPT,
        ),
    )
    event = runner_proto.RunEventReq(
        kind=runner_proto.RunEventReqKind.STEP,
        execution=execution,
        step=step,
    )

    async def dummy_coro() -> None:
        await asyncio.Event().wait()

    dummy_task = asyncio.create_task(dummy_coro())
    runner = DummyRunnerWithWorkflow(["node1"], execution=execution)
    frame = RunnerFrame(
        workflow_name="wf-ui-server-user-input",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )
    server.manager._runner_stack.append(frame)

    response_task = asyncio.create_task(server.on_runner_event(frame, event))

    request_envelope = await client_endpoint.recv()
    assert request_envelope.payload.kind == manager_proto.BasePacketKind.RUNNER_REQ
    initial_prompt_envelope = await client_endpoint.recv()
    initial_prompt_payload = initial_prompt_envelope.payload
    assert initial_prompt_payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(initial_prompt_payload, manager_proto.InputPromptPacket)
    waiter_task = asyncio.create_task(
        project.input_manager.wait_for_input(input_type=INPUT_TYPE_INTERACTIVE)
    )
    await asyncio.sleep(0)
    user_message = state.Message(role=models.Role.USER, text="user input message")
    user_input_packet = manager_proto.UserInputPacket(message=user_message)
    response_envelope = manager_proto.BasePacketEnvelope(
        msg_id=request_envelope.msg_id + 1,
        payload=user_input_packet,
    )
    await client_endpoint.send(response_envelope)

    server_incoming = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_incoming)
    assert handled is True

    published_message = await waiter_task
    assert published_message == user_message

    resp = await response_task
    assert resp is not None
    assert resp.resp_type == runner_proto.RunEventResponseType.NOOP
    assert resp.message is None

    prompt_envelope = await client_endpoint.recv()
    prompt_payload = prompt_envelope.payload
    assert prompt_payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(prompt_payload, manager_proto.InputPromptPacket)
    assert prompt_payload.title is None
    assert prompt_payload.subtitle is None

    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task


@pytest.mark.asyncio
async def test_uiserver_on_runner_event_user_input_prompt_confirm_title() -> None:
    history = HistoryManager()
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)

    execution = state.WorkflowExecution(workflow_name="wf-ui-server-user-input-confirm")
    node_execution = history.upsert_node_execution(
        execution,
        state.NodeExecution(
            node="node1",
            status=state.RunStatus.RUNNING,
        ),
    )
    step = history.upsert_step(
        execution,
        state.Step(
            execution_id=node_execution.id,
            type=state.StepType.PROMPT_CONFIRM,
        ),
    )
    event = runner_proto.RunEventReq(
        kind=runner_proto.RunEventReqKind.STEP,
        execution=execution,
        step=step,
    )

    async def dummy_coro() -> None:
        await asyncio.Event().wait()

    dummy_task = asyncio.create_task(dummy_coro())
    runner = DummyRunnerWithWorkflow(["node1"], execution=execution)
    frame = RunnerFrame(
        workflow_name="wf-ui-server-user-input-confirm",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )
    server.manager._runner_stack.append(frame)

    response_task = asyncio.create_task(server.on_runner_event(frame, event))

    _ = await client_endpoint.recv()
    prompt_envelope = await client_endpoint.recv()
    prompt_payload = prompt_envelope.payload
    assert prompt_payload.kind == manager_proto.BasePacketKind.INPUT_PROMPT
    assert isinstance(prompt_payload, manager_proto.InputPromptPacket)
    assert prompt_payload.title == "Press enter to confirm or provide a reply"
    waiter_task = asyncio.create_task(
        project.input_manager.wait_for_input(input_type=INPUT_TYPE_INTERACTIVE)
    )
    await asyncio.sleep(0)

    user_message = state.Message(role=models.Role.USER, text="")
    user_input_packet = manager_proto.UserInputPacket(message=user_message)
    response_envelope = manager_proto.BasePacketEnvelope(
        msg_id=prompt_envelope.msg_id + 1,
        payload=user_input_packet,
    )
    await client_endpoint.send(response_envelope)

    server_incoming = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_incoming)
    assert handled is True

    published_message = await waiter_task
    assert published_message == user_message

    resp = await response_task
    assert resp is not None
    assert resp.resp_type == runner_proto.RunEventResponseType.NOOP

    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task


@pytest.mark.asyncio
async def test_uiserver_runner_start_workflow_reports_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)
    await server.start()

    async def fake_start_workflow(
        self: BaseManager,
        wf_name: str,
        initial_message: Optional[state.Message] = None,
    ) -> object:
        _ = self
        _ = initial_message
        raise ValueError(f"workflow '{wf_name}' is invalid")

    monkeypatch.setattr(BaseManager, "start_workflow", fake_start_workflow)

    runner = DummyRunnerWithWorkflow(["node1"])
    frame = RunnerFrame(
        workflow_name="parent",
        runner=runner,  # type: ignore[arg-type]
        initial_message=None,
        agen=None,
    )
    event = runner_proto.RunEventReq(
        kind=runner_proto.RunEventReqKind.START_WORKFLOW,
        execution=runner.execution,
        start_workflow=runner_proto.RunEventStartWorkflow(
            workflow_name="broken-child",
            initial_message=None,
        ),
    )

    response = await server.on_runner_event(frame, event)

    assert response is not None
    assert response.message is not None
    assert "workflow 'broken-child' is invalid" in response.message.text

    resp_envelope = await client_endpoint.recv()
    assert isinstance(resp_envelope.payload, manager_proto.UIEventPacket)
    assert resp_envelope.payload.event.title == "Workflow validation failed"
    assert resp_envelope.payload.event.source == "broken-child"


@pytest.mark.asyncio
async def test_uiserver_status_event_emits_ui_state_packet() -> None:
    history = HistoryManager()
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
        model_name="chatgpt/gpt-5.4",
        input_token_limit=1000,
    )

    node_execution = history.upsert_node_execution(
        execution,
        state.NodeExecution(
            node="node-status",
            status=state.RunStatus.RUNNING,
        ),
    )
    stats = runner_proto.RunStats(
        status=state.RunnerStatus.RUNNING,
        current_node_name=node_execution.node,
        current_node_execution_id=node_execution.id,
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
    assert runner_state.node_execution_id == str(node_execution.id)
    assert runner_state.status == stats.status
    assert state_packet.last_step_llm_usage is not None
    assert state_packet.last_step_llm_usage.prompt_tokens == 10
    assert state_packet.last_step_llm_usage.completion_tokens == 5
    assert state_packet.last_step_llm_usage.model_name == "chatgpt/gpt-5.4"
    assert state_packet.last_step_llm_usage.input_token_limit == 1000

    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task
