from __future__ import annotations

import asyncio
from typing import AsyncIterator, Dict

import pytest

from vocode import models, state
from vocode.manager.base import BaseManager, RunnerFrame
from vocode.runner.base import BaseExecutor, ExecutorFactory, ExecutorInput
from vocode.runner.runner import Runner
from vocode.runner.proto import RunEventReqKind, RunEventResp, RunEventResponseType


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


class DummyWorkflow:
    def __init__(self, name: str, graph: models.Graph) -> None:
        self.name = name
        self.graph = graph


class FakeProject:
    def __init__(self) -> None:
        self.current_workflow: str | None = None

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
        if step.type == state.StepType.PROMPT:
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

    dummy_task = asyncio.create_task(asyncio.sleep(3600))
    frame = RunnerFrame(
        workflow_name="wf-manager-events",
        runner=runner,
        initial_message=initial_message,
        task=dummy_task,
    )
    manager._runner_stack.append(frame)

    runner_task = asyncio.create_task(
        manager._run_runner_task(
            workflow_name="wf-manager-events",
            runner=runner,
        )
    )

    await runner_task
    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task

    assert runner.status == state.RunnerStatus.FINISHED
    assert manager.runner_stack == []

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    assert set(node_execs_by_name.keys()) == {"node1"}
    node_exec = node_execs_by_name["node1"]

    output_steps = [s for s in events if s.type == state.StepType.OUTPUT_MESSAGE]
    prompt_steps = [s for s in events if s.type == state.StepType.PROMPT]
    assert output_steps
    assert prompt_steps

    prompt_steps_exec = [s for s in node_exec.steps if s.type == state.StepType.PROMPT]
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

    dummy_task = asyncio.create_task(asyncio.sleep(3600))
    frame = RunnerFrame(
        workflow_name="wf-manager-status",
        runner=runner,
        initial_message=initial_message,
        task=dummy_task,
    )
    manager._runner_stack.append(frame)

    runner_task = asyncio.create_task(
        manager._run_runner_task(
            workflow_name="wf-manager-status",
            runner=runner,
        )
    )

    await runner_task
    dummy_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await dummy_task

    assert runner.status == state.RunnerStatus.FINISHED
    assert steps
    assert frame.last_stats is not None
    assert frame.last_stats.status == state.RunnerStatus.FINISHED
    assert frame.last_stats.current_node_name == "node-status"
