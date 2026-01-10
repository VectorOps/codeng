from typing import AsyncIterator, Dict, Callable

import pytest

from vocode import state, models
from tests.stub_project import StubProject
from vocode.runner.base import BaseExecutor, ExecutorFactory, ExecutorInput
from vocode.runner.executors.input import InputNode
from vocode.runner.runner import Runner, RunEvent
from vocode.runner.proto import RunEventResp, RunEventResponseType


async def drive_runner(
    agen: AsyncIterator[RunEvent],
    handler: Callable[[RunEvent], RunEventResp],
    *,
    ignore_non_step: bool = False,
) -> list[RunEvent]:
    events: list[RunEvent] = []
    send: RunEventResp | None = None
    while True:
        try:
            if send is None:
                event = await agen.__anext__()
            else:
                event = await agen.asend(send)
        except StopAsyncIteration:
            break
        if ignore_non_step and event.step is None:
            send = RunEventResp(resp_type=RunEventResponseType.NOOP, message=None)
            continue
        events.append(event)
        send = handler(event)
    return events


@ExecutorFactory.register("fake")
class FakeExecutor(BaseExecutor):
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
                    ),
                    "is_complete": False,
                }
            )
            yield step1
            step2 = step1.model_copy(
                update={
                    "message": state.Message(
                        role=models.Role.ASSISTANT,
                        text=f"{text_prefix}-final",
                    ),
                    "is_complete": True,
                }
            )
            yield step2
        elif node_name == "node2":
            msg = state.Message(
                role=models.Role.ASSISTANT,
                text="node2-output",
            )
            step = state.Step(
                execution=execution,
                type=state.StepType.OUTPUT_MESSAGE,
                message=msg,
                outcome_name="go",
                is_complete=True,
            )
            yield step
        else:
            msg = state.Message(
                role=models.Role.ASSISTANT,
                text="terminal-output",
            )
            step = state.Step(
                execution=execution,
                type=state.StepType.OUTPUT_MESSAGE,
                message=msg,
                is_complete=True,
            )
            yield step


@ExecutorFactory.register("loop")
class LoopExecutor(BaseExecutor):
    def __init__(self, config: models.Node, project):
        super().__init__(config, project)
        self._run_counts: Dict[str, int] = {}

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        execution = inp.execution
        node_name = execution.node
        count = self._run_counts.get(node_name, 0) + 1
        self._run_counts[node_name] = count

        msg = state.Message(
            role=models.Role.ASSISTANT,
            text=f"loop-{count}",
        )
        interim_step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=msg,
            is_complete=False,
        )
        yield interim_step

        outcome = "again" if count == 1 else "done"
        final_step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=msg,
            outcome_name=outcome,
            is_complete=True,
        )
        yield final_step


@ExecutorFactory.register("tool-prompt")
class ToolPromptExecutor(BaseExecutor):
    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        execution = inp.execution
        has_tool_result = False
        for existing_step in execution.steps:
            if (
                existing_step.type == state.StepType.TOOL_RESULT
                and existing_step.is_complete
            ):
                has_tool_result = True
                break
        if not has_tool_result:
            tool_req = state.ToolCallReq(
                id="call-test-tool",
                name="test-tool",
                arguments={"x": 1},
            )
            msg = state.Message(
                role=models.Role.ASSISTANT,
                text="with tool",
                tool_call_requests=[tool_req],
            )
        else:
            msg = state.Message(
                role=models.Role.ASSISTANT,
                text="after tool",
            )
        step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=msg,
            is_complete=True,
        )
        yield step


class DummyWorkflow:
    def __init__(self, name: str, graph: models.Graph):
        self.name = name
        self.graph = graph


@ExecutorFactory.register("no-complete")
class NoCompleteExecutor(BaseExecutor):
    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        execution = inp.execution
        msg = state.Message(
            role=models.Role.ASSISTANT,
            text="no-complete",
        )
        step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=msg,
            is_complete=False,
        )
        yield step


@ExecutorFactory.register("multi-complete")
class MultiCompleteExecutor(BaseExecutor):
    type = "multi-complete"

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        execution = inp.execution
        msg1 = state.Message(
            role=models.Role.ASSISTANT,
            text="first",
        )
        step1 = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=msg1,
            is_complete=True,
        )
        yield step1

        msg2 = state.Message(
            role=models.Role.ASSISTANT,
            text="second",
        )
        step2 = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=msg2,
            is_complete=True,
        )
        yield step2


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
    prompt_count = 0

    def handler(event: RunEvent) -> RunEventResp:
        nonlocal prompt_count
        assert event.step is not None
        step = event.step
        execution = step.execution

        if step.type == state.StepType.PROMPT_CONFIRM and execution.node == "node1":
            prompt_count += 1
            if prompt_count == 1:
                return RunEventResp(
                    resp_type=RunEventResponseType.DECLINE,
                    message=None,
                )
            if prompt_count == 2:
                msg = state.Message(
                    role=models.Role.USER,
                    text="more please",
                )
                return RunEventResp(
                    resp_type=RunEventResponseType.MESSAGE,
                    message=msg,
                )
            msg = state.Message(
                role=models.Role.USER,
                text="",
            )
            return RunEventResp(
                resp_type=RunEventResponseType.MESSAGE,
                message=msg,
            )
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    events = await drive_runner(agen, handler, ignore_non_step=True)

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

    prompt_steps = [
        e.step for e in events if e.step.type == state.StepType.PROMPT_CONFIRM
    ]
    assert prompt_steps
    assert all(s.execution.node == "node1" for s in prompt_steps)

    node2_exec = node_execs_by_name["node2"]
    node2_complete_steps = [
        s
        for s in node2_exec.steps
        if s.type == state.StepType.OUTPUT_MESSAGE and s.is_complete
    ]
    assert node2_complete_steps
    assert node2_complete_steps[-1].outcome_name == "go"

    node3_exec = node_execs_by_name["node3"]
    assert node3_exec.status == state.RunStatus.FINISHED

    assert any(
        e.step is not None
        and isinstance(e.step, state.Step)
        and e.step.execution.node == "node1"
        for e in events
    )
    assert any(
        e.step is not None
        and isinstance(e.step, state.Step)
        and e.step.execution.node == "node2"
        for e in events
    )
    assert any(
        e.step is not None
        and isinstance(e.step, state.Step)
        and e.step.execution.node == "node3"
        for e in events
    )

    for ne in runner.execution.node_executions.values():
        node_steps = ne.steps
        if not node_steps:
            continue
        finals = [s for s in node_steps if s.is_final]
        assert len(finals) == 1
        last_complete_output = None
        for s in reversed(node_steps):
            if s.is_complete and s.type == state.StepType.OUTPUT_MESSAGE:
                last_complete_output = s
                break
        assert last_complete_output is not None
        assert finals[0] is last_complete_output


@pytest.mark.asyncio
async def test_runner_errors_when_executor_has_no_complete_step():
    node = models.Node(
        name="nocomp",
        type="no-complete",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-nocomp", graph=graph)

    initial_message = state.Message(
        role=models.Role.USER,
        text="start",
    )

    runner = Runner(
        workflow=workflow, project=object(), initial_message=initial_message
    )

    agen = runner.run()

    while True:
        event = await agen.__anext__()
        if event.step is not None:
            break

    assert event.step is not None
    assert event.step.execution.node == "nocomp"

    with pytest.raises(RuntimeError):
        await agen.asend(
            RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )
        )


@pytest.mark.asyncio
async def test_runner_errors_when_executor_has_multiple_complete_steps():
    node = models.Node(
        name="multicomp",
        type="multi-complete",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-multicomp", graph=graph)

    initial_message = state.Message(
        role=models.Role.USER,
        text="start",
    )

    runner = Runner(
        workflow=workflow, project=object(), initial_message=initial_message
    )

    agen = runner.run()

    while True:
        event1 = await agen.__anext__()
        if event1.step is not None:
            break

    assert event1.step is not None
    assert event1.step.execution.node == "multicomp"

    event2 = await agen.asend(
        RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )
    )
    while True:
        if event2.step is not None:
            break
        event2 = await agen.asend(
            RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )
        )
    assert event2.step.execution.node == "multicomp"

    with pytest.raises(RuntimeError):
        await agen.asend(
            RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )
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

    def handler(event: RunEvent) -> RunEventResp:
        return RunEventResp(resp_type=RunEventResponseType.NOOP, message=None)

    await drive_runner(agen, handler, ignore_non_step=True)

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

    def handler(event: RunEvent) -> RunEventResp:
        return RunEventResp(resp_type=RunEventResponseType.NOOP, message=None)

    await drive_runner(agen, handler, ignore_non_step=True)

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

    def handler(event: RunEvent) -> RunEventResp:
        return RunEventResp(resp_type=RunEventResponseType.NOOP, message=None)

    await drive_runner(agen, handler, ignore_non_step=True)

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    node2_exec = node_execs_by_name["node2"]

    assert len(node2_exec.input_messages) == 1
    combined = node2_exec.input_messages[0]
    assert combined.text == "hello\n\nrun1-final"
    assert combined.role == models.Role.ASSISTANT


@pytest.mark.asyncio
async def test_runner_stop_stops_execution_loop():
    node = models.Node(
        name="loop1",
        type="loop",
        outcomes=[
            models.OutcomeSlot(name="again"),
            models.OutcomeSlot(name="done"),
        ],
        confirmation=models.Confirmation.AUTO,
    )
    edges = [
        models.Edge(
            source_node="loop1",
            source_outcome="again",
            target_node="loop1",
        ),
        models.Edge(
            source_node="loop1",
            source_outcome="done",
            target_node="loop1",
        ),
    ]
    graph = models.Graph(nodes=[node], edges=edges)
    workflow = DummyWorkflow(name="wf-stop", graph=graph)

    initial_message = state.Message(
        role=models.Role.USER,
        text="start",
    )

    runner = Runner(
        workflow=workflow, project=object(), initial_message=initial_message
    )

    agen = runner.run()

    while True:
        event = await agen.__anext__()
        if event.step is not None:
            break

    assert event.step is not None
    assert event.step.execution.node == "loop1"
    assert runner.status == state.RunnerStatus.RUNNING

    runner.stop()
    saw_step_after_stop = False
    while True:
        try:
            event = await agen.asend(
                RunEventResp(
                    resp_type=RunEventResponseType.NOOP,
                    message=None,
                )
            )
        except StopAsyncIteration:
            break
        if event.step is not None:
            saw_step_after_stop = True
            break

    assert not saw_step_after_stop

    assert runner.status == state.RunnerStatus.STOPPED

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    assert set(node_execs_by_name.keys()) == {"loop1"}
    loop_exec = node_execs_by_name["loop1"]
    assert loop_exec.status == state.RunStatus.STOPPED


class ResumeSkipExecutor(BaseExecutor):
    type = "resume-skip"

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        raise AssertionError("ResumeSkipExecutor.run should not be called")


ExecutorFactory.register("resume-skip", ResumeSkipExecutor)


class ResumeRunExecutor(BaseExecutor):
    type = "resume-run"

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        execution = inp.execution
        msg = state.Message(
            role=models.Role.ASSISTANT,
            text="resumed-output",
        )
        step = state.Step(
            execution=execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=msg,
            is_complete=True,
        )
        yield step


ExecutorFactory.register("resume-run", ResumeRunExecutor)


@pytest.mark.asyncio
async def test_runner_resume_from_output_message_skips_executor_run():
    node = models.Node(
        name="node-output",
        type="resume-skip",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-resume-output", graph=graph)

    runner = Runner(
        workflow=workflow,
        project=object(),
        initial_message=None,
    )

    execution = state.NodeExecution(
        node="node-output",
        input_messages=[],
        steps=[],
        status=state.RunStatus.RUNNING,
    )
    runner.execution.node_executions[execution.id] = execution

    msg = state.Message(
        role=models.Role.ASSISTANT,
        text="existing-output",
    )
    output_step = state.Step(
        execution=execution,
        type=state.StepType.OUTPUT_MESSAGE,
        message=msg,
        is_complete=True,
    )
    execution.steps.append(output_step)
    runner.execution.steps.append(output_step)

    extra_step = state.Step(
        execution=execution,
        type=state.StepType.PROMPT,
        message=None,
        is_complete=True,
    )
    execution.steps.append(extra_step)
    runner.execution.steps.append(extra_step)

    agen = runner.run()

    with pytest.raises(StopAsyncIteration):
        while True:
            event = await agen.__anext__()
            if event.step is not None:
                raise AssertionError(
                    "Expected no step events when resuming from output message"
                )

    assert runner.status == state.RunnerStatus.FINISHED
    assert all(s.id != extra_step.id for s in runner.execution.steps)
    assert all(s.id != extra_step.id for s in execution.steps)


@pytest.mark.asyncio
async def test_runner_resume_from_input_message_re_runs_executor():
    node = models.Node(
        name="node-input",
        type="resume-run",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-resume-input", graph=graph)

    runner = Runner(
        workflow=workflow,
        project=object(),
        initial_message=None,
    )

    execution = state.NodeExecution(
        node="node-input",
        input_messages=[],
        steps=[],
        status=state.RunStatus.RUNNING,
    )
    runner.execution.node_executions[execution.id] = execution

    msg = state.Message(
        role=models.Role.USER,
        text="user input",
    )
    input_step = state.Step(
        execution=execution,
        type=state.StepType.INPUT_MESSAGE,
        message=msg,
        is_complete=True,
    )
    execution.steps.append(input_step)
    runner.execution.steps.append(input_step)

    agen = runner.run()
    events: list[RunEvent] = []

    def handler(event: RunEvent) -> RunEventResp:
        if event.step is not None:
            events.append(event)
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    await drive_runner(agen, handler, ignore_non_step=True)

    assert runner.status == state.RunnerStatus.FINISHED

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    node_exec = node_execs_by_name["node-input"]
    complete_outputs = [
        s
        for s in node_exec.steps
        if s.type == state.StepType.OUTPUT_MESSAGE and s.is_complete
    ]
    assert complete_outputs


@pytest.mark.asyncio
async def test_input_node_prompts_and_returns_user_message_as_output():
    node = InputNode(
        name="input-node",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
        message="Say something",
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-input-node", graph=graph)

    runner = Runner(
        workflow=workflow,
        project=object(),
        initial_message=None,
    )

    agen = runner.run()

    def handler(event: RunEvent) -> RunEventResp:
        if event.step is None:
            return RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )
        step = event.step
        if step.type == state.StepType.PROMPT and step.execution.node == "input-node":
            user_message = state.Message(
                role=models.Role.USER,
                text="user-input-text",
            )
            return RunEventResp(
                resp_type=RunEventResponseType.MESSAGE,
                message=user_message,
            )
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    events = await drive_runner(agen, handler, ignore_non_step=True)

    assert runner.status == state.RunnerStatus.FINISHED

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    assert set(node_execs_by_name.keys()) == {"input-node"}
    input_exec = node_execs_by_name["input-node"]

    prompt_steps = [s for s in input_exec.steps if s.type == state.StepType.PROMPT]
    input_steps = [
        s for s in input_exec.steps if s.type == state.StepType.INPUT_MESSAGE
    ]
    output_steps = [
        s
        for s in input_exec.steps
        if s.type == state.StepType.OUTPUT_MESSAGE and s.is_complete
    ]

    assert prompt_steps
    assert prompt_steps[-1].message is not None
    assert prompt_steps[-1].message.text == "Say something"

    assert input_steps
    assert output_steps

    final_output = output_steps[-1]
    assert final_output.message is not None
    assert final_output.message.text == "user-input-text"

    assert any(
        e.step is not None
        and e.step.type == state.StepType.PROMPT
        and e.step.execution.node == "input-node"
        for e in events
    )

    assert any(
        e.step is not None
        and e.step.type == state.StepType.INPUT_MESSAGE
        and e.step.execution.node == "input-node"
        and e.step.message is not None
        and e.step.message.text == "user-input-text"
        for e in events
    )


@pytest.mark.asyncio
async def test_runner_resume_from_tool_result_re_runs_executor():
    node = models.Node(
        name="node-tool",
        type="resume-run",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-resume-tool", graph=graph)

    runner = Runner(
        workflow=workflow,
        project=object(),
        initial_message=None,
    )

    execution = state.NodeExecution(
        node="node-tool",
        input_messages=[],
        steps=[],
        status=state.RunStatus.RUNNING,
    )
    runner.execution.node_executions[execution.id] = execution

    tool_resp = state.ToolCallResp(
        id="call-fn",
        name="fn",
        status=state.ToolCallStatus.COMPLETED,
        result={"ok": True},
    )
    msg = state.Message(
        role=models.Role.TOOL,
        text="",
        tool_call_responses=[tool_resp],
    )
    tool_step = state.Step(
        execution=execution,
        type=state.StepType.TOOL_RESULT,
        message=msg,
        is_complete=True,
    )
    execution.steps.append(tool_step)
    runner.execution.steps.append(tool_step)

    agen = runner.run()
    events: list[RunEvent] = []

    def handler(event: RunEvent) -> RunEventResp:
        if event.step is not None:
            events.append(event)
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    await drive_runner(agen, handler, ignore_non_step=True)

    assert runner.status == state.RunnerStatus.FINISHED

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    node_exec = node_execs_by_name["node-tool"]
    complete_outputs = [
        s
        for s in node_exec.steps
        if s.type == state.StepType.OUTPUT_MESSAGE and s.is_complete
    ]
    assert complete_outputs
    assert any(
        s.message is not None and s.message.text == "resumed-output"
        for s in complete_outputs
    )


@pytest.mark.asyncio
async def test_runner_emits_steps_for_tool_call_confirmation():
    node = models.Node(
        name="tool-node",
        type="tool-prompt",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-tool-confirmation", graph=graph)

    initial_message = state.Message(
        role=models.Role.USER,
        text="start",
    )

    runner = Runner(
        workflow=workflow,
        project=StubProject(),
        initial_message=initial_message,
    )

    agen = runner.run()

    def handler(event: RunEvent) -> RunEventResp:
        step = event.step
        if step is None:
            return RunEventResp(
                resp_type=RunEventResponseType.NOOP,
                message=None,
            )
        if (
            step.type == state.StepType.TOOL_REQUEST
            and step.message is not None
            and step.message.tool_call_requests
        ):
            return RunEventResp(
                resp_type=RunEventResponseType.DECLINE,
                message=state.Message(
                    role=models.Role.USER,
                    text="no thanks",
                ),
            )
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    events = await drive_runner(agen, handler, ignore_non_step=True)

    prompt_events = [
        e
        for e in events
        if e.step is not None
        and e.step.type == state.StepType.TOOL_REQUEST
        and e.step.message is not None
        and e.step.message.tool_call_requests
    ]
    assert prompt_events

    response_events = [
        e
        for e in events
        if e.step is not None
        and e.step.type == state.StepType.REJECTION
        and e.step.message is not None
        and e.step.message.text.strip()
    ]
    assert response_events

    assert runner.status == state.RunnerStatus.FINISHED


@pytest.mark.asyncio
async def test_runner_start_after_stop_resumes_execution():
    node = models.Node(
        name="node1",
        type="fake",
        outcomes=[],
        confirmation=models.Confirmation.AUTO,
    )
    graph = models.Graph(nodes=[node], edges=[])
    workflow = DummyWorkflow(name="wf-start-after-stop", graph=graph)

    initial_message = state.Message(
        role=models.Role.USER,
        text="start",
    )

    runner = Runner(
        workflow=workflow,
        project=object(),
        initial_message=initial_message,
    )

    agen1 = runner.run()

    while True:
        event1 = await agen1.__anext__()
        if event1.step is not None:
            break

    assert event1.step is not None
    assert event1.step.execution.node == "node1"
    assert runner.status == state.RunnerStatus.RUNNING

    runner.stop()
    saw_step_after_stop = False
    while True:
        try:
            event = await agen1.asend(
                RunEventResp(
                    resp_type=RunEventResponseType.NOOP,
                    message=None,
                )
            )
        except StopAsyncIteration:
            break
        if event.step is not None:
            saw_step_after_stop = True
            break

    assert not saw_step_after_stop

    assert runner.status == state.RunnerStatus.STOPPED

    agen2 = runner.run()
    events: list[RunEvent] = []

    def handler(event: RunEvent) -> RunEventResp:
        if event.step is not None:
            events.append(event)
        return RunEventResp(
            resp_type=RunEventResponseType.NOOP,
            message=None,
        )

    await drive_runner(agen2, handler, ignore_non_step=True)

    assert runner.status == state.RunnerStatus.FINISHED

    node_execs_by_name: Dict[str, state.NodeExecution] = {}
    for ne in runner.execution.node_executions.values():
        node_execs_by_name[ne.node] = ne

    assert set(node_execs_by_name.keys()) == {"node1"}
    node_exec = node_execs_by_name["node1"]
    assert node_exec.status == state.RunStatus.FINISHED
    complete_outputs = [
        s
        for s in node_exec.steps
        if s.type == state.StepType.OUTPUT_MESSAGE and s.is_complete
    ]
    assert complete_outputs
