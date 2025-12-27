from typing import AsyncIterator, Dict

import pytest

from vocode import state, models
from vocode.runner.base import BaseExecutor, ExecutorInput
from vocode.runner.runner import Runner, RunEvent
from vocode.runner.proto import RunEventResp, RunEventResponseType


class FakeExecutor(BaseExecutor):
    type = "fake"

    def __init__(self, config: models.Node, project):
        super().__init__(config, project)
        self._call_counts: Dict[str, int] = {}

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        execution = inp.execution
        node_name = execution.node
        key = str(execution.id)
        count = self._call_counts.get(key, 0) + 1
        self._call_counts[key] = count

        if node_name == "node1":
            text_prefix = "run1" if count == 1 else "run2"
            base_step = state.Step(
                execution=execution,
                type=state.StepType.OUTPUT_MESSAGE,
            )
            step1 = base_step.model_copy(
                update={
                    "message": state.Message(
                        role=models.Role.ASSISTANT,
                        text=f"{text_prefix}-partial",
                    )
                }
            )
            yield step1
            step2 = step1.model_copy(
                update={
                    "message": state.Message(
                        role=models.Role.ASSISTANT,
                        text=f"{text_prefix}-final",
                    )
                }
            )
            yield step2
            completion = state.Step(
                execution=execution,
                type=state.StepType.COMPLETION,
            )
            yield completion
        elif node_name == "node2":
            msg = state.Message(
                role=models.Role.ASSISTANT,
                text="node2-output",
            )
            step = state.Step(
                execution=execution,
                type=state.StepType.OUTPUT_MESSAGE,
                message=msg,
            )
            yield step
            completion = state.Step(
                execution=execution,
                type=state.StepType.COMPLETION,
                outcome_name="go",
            )
            yield completion
        else:
            msg = state.Message(
                role=models.Role.ASSISTANT,
                text="terminal-output",
            )
            step = state.Step(
                execution=execution,
                type=state.StepType.OUTPUT_MESSAGE,
                message=msg,
            )
            yield step
            completion = state.Step(
                execution=execution,
                type=state.StepType.COMPLETION,
            )
            yield completion


BaseExecutor.register("fake", FakeExecutor)


class DummyWorkflow:
    def __init__(self, name: str, graph: models.Graph):
        self.name = name
        self.graph = graph


@pytest.mark.asyncio
async def test_runner_execution_flow():
    node1 = models.Node(
        name="node1",
        type="fake",
        outcomes=[models.OutcomeSlot(name="branch")],
        confirmation=models.Confirmation.MANUAL,
    )
    node2 = models.Node(
        name="node2",
        type="fake",
        outcomes=[
            models.OutcomeSlot(name="go"),
            models.OutcomeSlot(name="stop"),
        ],
        confirmation=models.Confirmation.AUTO,
    )
    node3 = models.Node(
        name="node3",
        type="fake",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    edges = [
        models.Edge(
            source_node="node1",
            source_outcome="branch",
            target_node="node2",
        ),
        models.Edge(
            source_node="node2",
            source_outcome="go",
            target_node="node3",
        ),
        models.Edge(
            source_node="node2",
            source_outcome="stop",
            target_node="node3",
        ),
    ]
    graph = models.Graph(nodes=[node1, node2, node3], edges=edges)
    workflow = DummyWorkflow(name="test-workflow", graph=graph)

    initial_message = state.Message(
        role=models.Role.USER,
        text="hello",
    )

    runner = Runner(
        workflow=workflow, project=object(), initial_message=initial_message
    )

    agen = runner.run()
    events: list[RunEvent] = []
    prompt_count = 0
    ack: RunEventResp | None = None

    while True:
        try:
            if ack is None:
                event = await agen.__anext__()
            else:
                event = await agen.asend(ack)
        except StopAsyncIteration:
            break

        events.append(event)
        step = event.step
        execution = step.execution

        if step.type == state.StepType.PROMPT and execution.node == "node1":
            prompt_count += 1
            if prompt_count == 1:
                ack = RunEventResp(
                    resp_type=RunEventResponseType.DECLINE,
                    message=None,
                )
            elif prompt_count == 2:
                msg = state.Message(
                    role=models.Role.USER,
                    text="more please",
                )
                ack = RunEventResp(
                    resp_type=RunEventResponseType.MESSAGE,
                    message=msg,
                )
            else:
                msg = state.Message(
                    role=models.Role.USER,
                    text="",
                )
                ack = RunEventResp(
                    resp_type=RunEventResponseType.MESSAGE,
                    message=msg,
                )
        else:
            ack = RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )

    assert runner.status == state.RunnerStatus.FINISHED

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    assert set(node_execs_by_name.keys()) == {"node1", "node2", "node3"}

    node1_exec = node_execs_by_name["node1"]
    assert node1_exec.input_messages
    assert node1_exec.input_messages[0].text == "hello"

    node1_output_steps = [
        s
        for s in node1_exec.steps
        if s.type == state.StepType.OUTPUT_MESSAGE and s.message is not None
    ]
    assert any("run1-final" in s.message.text for s in node1_output_steps)
    assert any("run2-final" in s.message.text for s in node1_output_steps)

    node1_input_steps = [
        s for s in node1_exec.steps if s.type == state.StepType.INPUT_MESSAGE
    ]
    assert len(node1_input_steps) >= 2

    prompt_steps = [e.step for e in events if e.step.type == state.StepType.PROMPT]
    assert prompt_steps
    assert all(s.execution.node == "node1" for s in prompt_steps)

    node2_exec = node_execs_by_name["node2"]
    node2_completion = [
        s for s in node2_exec.steps if s.type == state.StepType.COMPLETION
    ]
    assert node2_completion
    assert node2_completion[-1].outcome_name == "go"

    node3_exec = node_execs_by_name["node3"]
    assert node3_exec.status == state.RunStatus.FINISHED

    assert any(
        isinstance(e.step, state.Step) and e.step.execution.node == "node1"
        for e in events
    )
    assert any(
        isinstance(e.step, state.Step) and e.step.execution.node == "node2"
        for e in events
    )
    assert any(
        isinstance(e.step, state.Step) and e.step.execution.node == "node3"
        for e in events
    )


@pytest.mark.asyncio
async def test_result_mode_final_response_forwards_final_message():
    node1 = models.Node(
        name="node1",
        type="fake",
        outcomes=[models.OutcomeSlot(name="branch")],
        confirmation=models.Confirmation.AUTO,
        message_mode=models.ResultMode.FINAL_RESPONSE,
    )
    node2 = models.Node(
        name="node2",
        type="fake",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    edges = [
        models.Edge(
            source_node="node1",
            source_outcome="branch",
            target_node="node2",
        ),
    ]
    graph = models.Graph(nodes=[node1, node2], edges=edges)
    workflow = DummyWorkflow(name="wf-final-response", graph=graph)

    initial_message = state.Message(
        role=models.Role.USER,
        text="hello",
    )

    runner = Runner(
        workflow=workflow, project=object(), initial_message=initial_message
    )

    agen = runner.run()
    ack: RunEventResp | None = None

    while True:
        try:
            if ack is None:
                event = await agen.__anext__()
            else:
                event = await agen.asend(ack)
        except StopAsyncIteration:
            break

        ack = RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    node1_exec = node_execs_by_name["node1"]
    node2_exec = node_execs_by_name["node2"]

    assert [m.text for m in node1_exec.input_messages] == ["hello"]
    assert [m.text for m in node2_exec.input_messages] == ["run1-final"]


@pytest.mark.asyncio
async def test_result_mode_all_messages_forwards_all_messages():
    node1 = models.Node(
        name="node1",
        type="fake",
        outcomes=[models.OutcomeSlot(name="branch")],
        confirmation=models.Confirmation.AUTO,
        message_mode=models.ResultMode.ALL_MESSAGES,
    )
    node2 = models.Node(
        name="node2",
        type="fake",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    edges = [
        models.Edge(
            source_node="node1",
            source_outcome="branch",
            target_node="node2",
        ),
    ]
    graph = models.Graph(nodes=[node1, node2], edges=edges)
    workflow = DummyWorkflow(name="wf-all-messages", graph=graph)

    initial_message = state.Message(
        role=models.Role.USER,
        text="hello",
    )

    runner = Runner(
        workflow=workflow, project=object(), initial_message=initial_message
    )

    agen = runner.run()
    ack: RunEventResp | None = None

    while True:
        try:
            if ack is None:
                event = await agen.__anext__()
            else:
                event = await agen.asend(ack)
        except StopAsyncIteration:
            break

        ack = RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    node1_exec = node_execs_by_name["node1"]
    node2_exec = node_execs_by_name["node2"]

    texts_node1 = [m.text for m in node1_exec.input_messages]
    assert texts_node1 == ["hello"]

    texts_node2 = [m.text for m in node2_exec.input_messages]
    assert texts_node2 == ["hello", "run1-final"]


@pytest.mark.asyncio
async def test_result_mode_concatenate_final_builds_single_message():
    node1 = models.Node(
        name="node1",
        type="fake",
        outcomes=[models.OutcomeSlot(name="branch")],
        confirmation=models.Confirmation.AUTO,
        message_mode=models.ResultMode.CONCATENATE_FINAL,
    )
    node2 = models.Node(
        name="node2",
        type="fake",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    edges = [
        models.Edge(
            source_node="node1",
            source_outcome="branch",
            target_node="node2",
        ),
    ]
    graph = models.Graph(nodes=[node1, node2], edges=edges)
    workflow = DummyWorkflow(name="wf-concat-final", graph=graph)

    initial_message = state.Message(
        role=models.Role.USER,
        text="hello",
    )

    runner = Runner(
        workflow=workflow, project=object(), initial_message=initial_message
    )

    agen = runner.run()
    ack: RunEventResp | None = None

    while True:
        try:
            if ack is None:
                event = await agen.__anext__()
            else:
                event = await agen.asend(ack)
        except StopAsyncIteration:
            break

        ack = RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    node2_exec = node_execs_by_name["node2"]

    assert len(node2_exec.input_messages) == 1
    combined = node2_exec.input_messages[0]
    assert combined.text == "hello\n\nrun1-final"
    assert combined.role == models.Role.ASSISTANT
