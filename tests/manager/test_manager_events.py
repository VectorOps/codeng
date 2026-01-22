from __future__ import annotations

import asyncio
from typing import AsyncIterator, Dict, Optional

import pytest

from vocode import models, state
from vocode.manager.base import BaseManager, RunnerFrame
from vocode.persistence import state_manager as persistence_state_manager
from vocode.runner.base import BaseExecutor, ExecutorFactory, ExecutorInput
from vocode.runner.runner import Runner
from vocode.runner.proto import (
    RunEventReq,
    RunEventReqKind,
    RunEventResp,
    RunEventResponseType,
)


@ExecutorFactory.register("manager-test")
class ManagerTestExecutor(BaseExecutor):
    type = "manager-test"

    def __init__(self, config: models.Node, project) -> None:
        super().__init__(config, project)

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        execution = inp.execution
        msg = state.Message(
            role=models.Role.ASSISTANT,
            text="manager-output",
        )
        step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=msg,
            is_complete=True,
        )
        yield step


block_event: asyncio.Event | None = None


@ExecutorFactory.register("manager-blocking")
class ManagerBlockingExecutor(BaseExecutor):
    type = "manager-blocking"

    def __init__(self, config: models.Node, project) -> None:
        super().__init__(config, project)

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        assert block_event is not None
        await block_event.wait()
        execution = inp.execution
        msg = state.Message(
            role=models.Role.ASSISTANT,
            text="blocking-output",
        )
        step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=msg,
            is_complete=True,
        )
        yield step


class DummyWorkflow:
    def __init__(
        self,
        name: str,
        graph: models.Graph,
        need_input: bool = False,
        need_input_prompt: str | None = None,
    ) -> None:
        self.name = name
        self.graph = graph
        self.need_input = need_input
        self.need_input_prompt = need_input_prompt


class FakeProject:
    def __init__(self) -> None:
        self.current_workflow: str | None = None
        self.settings = None
        self.tools: dict[str, object] = {}
        self.state_manager = persistence_state_manager.NullWorkflowStateManager()

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


@pytest.mark.asyncio
async def test_manager_run_event_subscriber_emits_and_handles_responses() -> None:
    node = models.Node(
        name="node1",
        type="manager-test",
        outcomes=[],
        confirmation=models.Confirmation.MANUAL,
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-manager-events", graph=graph)

    project = FakeProject()

    initial_message = state.Message(
        role=models.Role.USER,
        text="hello",
    )

    runner = Runner(
        workflow=workflow,
        project=project,
        initial_message=initial_message,
    )

    events: list[state.Step] = []

    async def run_event_listener(
        frame: RunnerFrame,
        event,
    ) -> RunEventResp | None:
        assert frame.runner is runner
        if event.kind == RunEventReqKind.STATUS:
            return RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )
        assert event.step is not None
        step = event.step
        events.append(step)
        if step.type in (state.StepType.PROMPT, state.StepType.PROMPT_CONFIRM):
            return RunEventResp(
                resp_type=RunEventResponseType.APPROVE,
                message=None,
            )
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    manager = BaseManager(
        project=project,  # type: ignore[arg-type]
        run_event_listener=run_event_listener,
    )

    frame = RunnerFrame(
        workflow_name="wf-manager-events",
        runner=runner,
        initial_message=initial_message,
        agen=runner.run(),
    )
    manager._runner_stack.append(frame)

    runner_task = asyncio.create_task(manager._run_runner_task())

    await runner_task

    assert runner.status == state.RunnerStatus.FINISHED
    assert manager.runner_stack == []

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    assert set(node_execs_by_name.keys()) == {"node1"}
    node_exec = node_execs_by_name["node1"]

    output_steps = [s for s in events if s.type == state.StepType.OUTPUT_MESSAGE]
    prompt_steps = [s for s in events if s.type == state.StepType.PROMPT_CONFIRM]
    assert output_steps
    assert prompt_steps

    prompt_steps_exec = [
        s for s in node_exec.steps if s.type == state.StepType.PROMPT_CONFIRM
    ]
    approval_steps = [s for s in node_exec.steps if s.type == state.StepType.APPROVAL]
    assert prompt_steps_exec
    assert approval_steps


@pytest.mark.asyncio
async def test_manager_status_events_are_stored_and_not_forwarded() -> None:
    node = models.Node(
        name="node-status",
        type="manager-test",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-manager-status", graph=graph)

    project = FakeProject()

    initial_message = state.Message(
        role=models.Role.USER,
        text="hello",
    )

    runner = Runner(
        workflow=workflow,
        project=project,  # type: ignore[arg-type]
        initial_message=initial_message,
    )

    steps: list[state.Step] = []

    async def run_event_listener(
        frame: RunnerFrame,
        event,
    ) -> RunEventResp | None:
        if event.kind == RunEventReqKind.STATUS:
            return RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )
        assert event.kind == RunEventReqKind.STEP
        assert event.step is not None
        steps.append(event.step)
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    manager = BaseManager(
        project=project,  # type: ignore[arg-type]
        run_event_listener=run_event_listener,
    )

    frame = RunnerFrame(
        workflow_name="wf-manager-status",
        runner=runner,
        initial_message=initial_message,
        agen=runner.run(),
    )
    manager._runner_stack.append(frame)

    runner_task = asyncio.create_task(manager._run_runner_task())

    await runner_task

    assert runner.status == state.RunnerStatus.FINISHED
    assert steps
    assert frame.last_stats is not None
    assert frame.last_stats.status == state.RunnerStatus.FINISHED
    assert frame.last_stats.current_node_name == "node-status"


@pytest.mark.asyncio
async def test_manager_emits_final_status_on_runner_stop() -> None:
    node = models.Node(
        name="node-stop-final-status",
        type="manager-blocking",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-manager-stop-status", graph=graph)

    project = FakeProject()

    initial_message = state.Message(
        role=models.Role.USER,
        text="hello",
    )

    global block_event
    block_event = asyncio.Event()

    runner = Runner(
        workflow=workflow,
        project=project,  # type: ignore[arg-type]
        initial_message=initial_message,
    )

    status_events: list[state.RunnerStatus] = []

    async def run_event_listener(
        frame: RunnerFrame,
        event,
    ) -> RunEventResp | None:
        if event.kind == RunEventReqKind.STATUS:
            assert event.stats is not None
            status_events.append(event.stats.status)
            return RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    manager = BaseManager(
        project=project,  # type: ignore[arg-type]
        run_event_listener=run_event_listener,
    )

    frame = RunnerFrame(
        workflow_name="wf-manager-stop-status",
        runner=runner,
        initial_message=initial_message,
        agen=runner.run(),
    )
    manager._runner_stack.append(frame)

    manager._driver_task = asyncio.create_task(manager._run_runner_task())

    while not status_events:
        await asyncio.sleep(0)

    await manager.stop_current_runner()
    await asyncio.sleep(0)

    assert block_event is not None
    block_event.set()

    assert runner.status == state.RunnerStatus.STOPPED
    assert state.RunnerStatus.RUNNING in status_events
    assert state.RunnerStatus.STOPPED in status_events


@pytest.mark.asyncio
async def test_manager_edit_history_replaces_last_user_input_and_resumes() -> None:
    node = models.Node(
        name="node-edit",
        type="manager-test",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-manager-edit", graph=graph)

    project = FakeProject()

    initial_message = state.Message(
        role=models.Role.USER,
        text="initial",
    )

    runner = Runner(
        workflow=workflow,
        project=project,  # type: ignore[arg-type]
        initial_message=initial_message,
    )

    execution = runner.execution
    node_execution = state.NodeExecution(
        node="node-edit",
        status=state.RunStatus.RUNNING,
    )
    execution.node_executions[node_execution.id] = node_execution

    prompt_message = state.Message(
        role=models.Role.ASSISTANT,
        text="prompt",
    )
    prompt_step = state.Step(
        execution=node_execution,
        type=state.StepType.PROMPT,
        message=prompt_message,
        is_complete=True,
    )
    user_message = state.Message(
        role=models.Role.USER,
        text="old user input",
    )
    input_step = state.Step(
        execution=node_execution,
        type=state.StepType.INPUT_MESSAGE,
        message=user_message,
        is_complete=True,
    )
    output_message = state.Message(
        role=models.Role.ASSISTANT,
        text="after input",
    )
    output_step = state.Step(
        execution=node_execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=output_message,
        is_complete=True,
    )

    node_execution.steps.extend([prompt_step, input_step, output_step])
    execution.steps.extend([prompt_step, input_step, output_step])

    runner.status = state.RunnerStatus.STOPPED

    async def run_event_listener(
        frame: RunnerFrame,
        event,
    ) -> RunEventResp | None:
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    manager = BaseManager(
        project=project,  # type: ignore[arg-type]
        run_event_listener=run_event_listener,
    )

    frame = RunnerFrame(
        workflow_name="wf-manager-edit",
        runner=runner,
        initial_message=initial_message,
        agen=None,
    )
    manager._runner_stack.append(frame)

    edited = await manager.edit_history_with_text("new user input")
    assert edited is True

    all_steps = runner.execution.steps
    assert all_steps
    assert all_steps[-1].type == state.StepType.INPUT_MESSAGE
    assert all_steps[-1].message is not None
    assert all_steps[-1].message.text == "new user input"
    assert all(
        step.message is None or step.message.text != "old user input"
        for step in all_steps
    )


@pytest.mark.asyncio
async def test_manager_edit_history_stops_parent_runner_when_going_up_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = FakeProject()

    parent_node = models.Node(
        name="node-parent",
        type="manager-test",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    child_node = models.Node(
        name="node-child",
        type="manager-test",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )

    parent_workflow = DummyWorkflow(
        name="wf-parent",
        graph=models.Graph(nodes=[parent_node], edges=[]),
    )
    child_workflow = DummyWorkflow(
        name="wf-child",
        graph=models.Graph(nodes=[child_node], edges=[]),
    )

    parent_runner = Runner(
        workflow=parent_workflow,
        project=project,  # type: ignore[arg-type]
        initial_message=None,
    )
    child_runner = Runner(
        workflow=child_workflow,
        project=project,  # type: ignore[arg-type]
        initial_message=None,
    )

    parent_execution = parent_runner.execution
    parent_node_execution = state.NodeExecution(
        node="node-parent",
        status=state.RunStatus.RUNNING,
    )
    parent_execution.node_executions[parent_node_execution.id] = parent_node_execution

    prompt_message = state.Message(
        role=models.Role.ASSISTANT,
        text="parent prompt",
    )
    prompt_step = state.Step(
        execution=parent_node_execution,
        type=state.StepType.PROMPT,
        message=prompt_message,
        is_complete=True,
    )
    user_message = state.Message(
        role=models.Role.USER,
        text="parent input",
    )
    input_step = state.Step(
        execution=parent_node_execution,
        type=state.StepType.INPUT_MESSAGE,
        message=user_message,
        is_complete=True,
    )
    parent_node_execution.steps.extend([prompt_step, input_step])
    parent_execution.steps.extend([prompt_step, input_step])

    parent_runner.status = state.RunnerStatus.RUNNING
    child_runner.status = state.RunnerStatus.STOPPED

    async def run_event_listener(
        frame: RunnerFrame,
        event,
    ) -> RunEventResp | None:
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    manager = BaseManager(
        project=project,  # type: ignore[arg-type]
        run_event_listener=run_event_listener,
    )

    async def dummy_gen():
        if False:
            yield  # pragma: no cover

    parent_agen = dummy_gen()

    parent_frame = RunnerFrame(
        workflow_name="wf-parent",
        runner=parent_runner,
        initial_message=None,
        agen=parent_agen,
    )
    child_frame = RunnerFrame(
        workflow_name="wf-child",
        runner=child_runner,
        initial_message=None,
        agen=None,
    )

    manager._runner_stack.append(parent_frame)
    manager._runner_stack.append(child_frame)

    called: list[RunnerFrame] = []

    async def fake_stop_runner_frame(frame: RunnerFrame) -> Optional[RunEventReq]:
        called.append(frame)
        frame.runner.status = state.RunnerStatus.STOPPED
        return None

    monkeypatch.setattr(manager, "_stop_runner_frame", fake_stop_runner_frame)

    edited = await manager.edit_history_with_text("edited parent input")
    assert edited is True

    assert len(manager._runner_stack) == 1
    assert manager._runner_stack[0] is parent_frame
    assert called
    assert called[-1] is parent_frame
