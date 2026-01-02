from __future__ import annotations

import asyncio

import pytest

from vocode import models, state
from vocode.manager.base import RunnerFrame
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

    ack_packet = manager_proto.AckPacket()
    response_envelope = manager_proto.BasePacketEnvelope(
        msg_id=request_envelope.msg_id + 1,
        payload=ack_packet,
        source_msg_id=request_envelope.msg_id,
    )
    await client_endpoint.send(response_envelope)

    server_incoming = await server_endpoint.recv()
    handled = await server.on_ui_packet(server_incoming)
    assert handled is True

    resp = await response_task
    assert resp is not None
    assert resp.resp_type == runner_proto.RunEventResponseType.NOOP
    assert resp.message is None

    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task


@pytest.mark.asyncio
async def test_uiserver_status_event_emits_ui_state_packet() -> None:
    project = StubProject()
    server_endpoint, client_endpoint = InMemoryEndpoint.pair()
    server = UIServer(project=project, endpoint=server_endpoint)
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
