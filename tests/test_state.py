from uuid import uuid4

import pytest

from vocode import state, models
from vocode.runner.base import BaseExecutor, ExecutorInput, iter_execution_messages


def _make_message(text: str = "msg") -> state.Message:
    return state.Message(role=models.Role.USER, text=text)


def _make_node_execution(name: str) -> state.NodeExecution:
    return state.NodeExecution(
        node=name,
        input_messages=[_make_message()],
        status=state.RunStatus.RUNNING,
    )


def test_delete_steps_removes_from_workflow_and_node_executions() -> None:
    exec1 = _make_node_execution("node-1")
    exec2 = _make_node_execution("node-2")

    step1 = state.Step(execution=exec1, type=state.StepType.OUTPUT_MESSAGE)
    step2 = state.Step(execution=exec1, type=state.StepType.INPUT_MESSAGE)
    step3 = state.Step(execution=exec2, type=state.StepType.OUTPUT_MESSAGE)

    run = state.WorkflowExecution(
        workflow_name="test",
        node_executions={exec1.id: exec1, exec2.id: exec2},
        steps_by_id={step1.id: step1, step2.id: step2, step3.id: step3},
        step_ids=[step1.id, step2.id, step3.id],
    )
    exec1.step_ids = [step1.id, step2.id]
    exec2.step_ids = [step3.id]
    run.attach_runtime_refs()

    run.delete_steps([step1.id, step3.id])

    remaining_ids = {s.id for s in run.steps}
    assert remaining_ids == {step2.id}

    exec1_ids = {s.id for s in exec1.steps}
    exec2_ids = {s.id for s in exec2.steps}
    assert exec1_ids == {step2.id}
    assert exec2_ids == set()


def test_delete_steps_ignores_unknown_ids_and_empty_input() -> None:
    exec1 = _make_node_execution("node-1")
    step1 = state.Step(execution=exec1, type=state.StepType.OUTPUT_MESSAGE)
    run = state.WorkflowExecution(
        workflow_name="test",
        node_executions={exec1.id: exec1},
        steps_by_id={step1.id: step1},
        step_ids=[step1.id],
    )
    exec1.step_ids = [step1.id]
    run.attach_runtime_refs()

    unknown_id = uuid4()
    run.delete_steps([unknown_id])

    assert [s.id for s in run.steps] == [step1.id]
    assert [s.id for s in exec1.steps] == [step1.id]

    run.delete_steps([])

    assert [s.id for s in run.steps] == [step1.id]
    assert [s.id for s in exec1.steps] == [step1.id]


def test_delete_step_delegates_to_delete_steps() -> None:
    exec1 = _make_node_execution("node-1")
    step1 = state.Step(execution=exec1, type=state.StepType.OUTPUT_MESSAGE)
    run = state.WorkflowExecution(
        workflow_name="test",
        node_executions={exec1.id: exec1},
        steps_by_id={step1.id: step1},
        step_ids=[step1.id],
    )
    exec1.step_ids = [step1.id]
    run.attach_runtime_refs()

    run.delete_step(step1.id)

    assert run.steps == []
    assert exec1.steps == []


def test_step_is_final_defaults_false() -> None:
    exec1 = _make_node_execution("node-1")
    step = state.Step(execution=exec1, type=state.StepType.OUTPUT_MESSAGE)
    assert step.is_final is False


def test_iter_execution_messages_traverses_previous_chain_in_order() -> None:
    exec1 = state.NodeExecution(
        node="node",
        input_messages=[
            state.Message(role=models.Role.USER, text="in-1-a"),
            state.Message(role=models.Role.USER, text="in-1-b"),
        ],
        status=state.RunStatus.RUNNING,
    )
    step1 = state.Step(
        execution=exec1,
        type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.ASSISTANT, text="out-1-a"),
    )
    step2 = state.Step(
        execution=exec1,
        type=state.StepType.INPUT_MESSAGE,
        message=state.Message(role=models.Role.USER, text="in-1-c"),
    )
    exec2 = state.NodeExecution(
        node="node",
        input_messages=[
            state.Message(role=models.Role.USER, text="in-2-a"),
        ],
        status=state.RunStatus.RUNNING,
        previous=exec1,
    )
    step3 = state.Step(
        execution=exec2,
        type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.ASSISTANT, text="out-2-a"),
    )
    run = state.WorkflowExecution(
        workflow_name="test",
        node_executions={exec1.id: exec1, exec2.id: exec2},
        steps_by_id={step1.id: step1, step2.id: step2, step3.id: step3},
        step_ids=[step1.id, step2.id, step3.id],
    )
    exec1.step_ids = [step1.id, step2.id]
    exec2.step_ids = [step3.id]
    run.attach_runtime_refs()

    texts = [m.text for (m, _t) in iter_execution_messages(exec2)]
    assert texts == ["in-1-a", "in-1-b", "out-1-a", "in-1-c", "in-2-a", "out-2-a"]


def test_delete_node_execution_removes_execution_and_child_steps() -> None:
    exec1 = _make_node_execution("node-1")
    exec2 = _make_node_execution("node-2")

    step1 = state.Step(execution=exec1, type=state.StepType.OUTPUT_MESSAGE)
    step2 = state.Step(execution=exec1, type=state.StepType.INPUT_MESSAGE)
    step3 = state.Step(execution=exec2, type=state.StepType.OUTPUT_MESSAGE)

    run = state.WorkflowExecution(
        workflow_name="test",
        node_executions={exec1.id: exec1, exec2.id: exec2},
        steps_by_id={step1.id: step1, step2.id: step2, step3.id: step3},
        step_ids=[step1.id, step2.id, step3.id],
    )
    exec1.step_ids = [step1.id, step2.id]
    exec2.step_ids = [step3.id]
    run.attach_runtime_refs()

    run.delete_node_execution(exec1.id)

    assert exec1.id not in run.node_executions
    assert exec2.id in run.node_executions

    remaining_step_ids = {s.id for s in run.steps}
    assert remaining_step_ids == {step3.id}

    assert exec1.steps == []
    assert exec2.steps == [step3]


def test_reference_write_updates_concrete_id_fields() -> None:
    exec1 = state.NodeExecution(
        node="node",
        input_messages=[
            state.Message(role=models.Role.USER, text="in-1-a"),
            state.Message(role=models.Role.USER, text="in-1-b"),
        ],
        status=state.RunStatus.RUNNING,
    )
    step1 = state.Step(
        execution=exec1,
        type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.ASSISTANT, text="out-1-a"),
    )
    step2 = state.Step(
        execution=exec1,
        type=state.StepType.INPUT_MESSAGE,
        message=state.Message(role=models.Role.USER, text="in-1-c"),
    )

    exec2 = state.NodeExecution(
        node="node",
        input_messages=[
            state.Message(role=models.Role.USER, text="in-2-a"),
        ],
        status=state.RunStatus.RUNNING,
        previous=exec1,
    )
    step3 = state.Step(
        execution=exec2,
        type=state.StepType.OUTPUT_MESSAGE,
        message=state.Message(role=models.Role.ASSISTANT, text="out-2-a"),
    )
    run = state.WorkflowExecution(workflow_name="wf")
    run.node_executions = {exec1.id: exec1, exec2.id: exec2}
    run.messages_by_id = {
        message.id: message
        for message in [
            *exec1.input_messages,
            *exec2.input_messages,
            step1.message,
            step2.message,
            step3.message,
        ]
        if message is not None
    }
    run.steps_by_id = {step1.id: step1, step2.id: step2, step3.id: step3}
    run.step_ids = [step1.id, step2.id, step3.id]
    exec1.step_ids = [step1.id, step2.id]
    exec2.step_ids = [step3.id]
    run.attach_runtime_refs()

    assert step1.execution_id == exec1.id
    assert step1.message_id == step1.message.id
    assert exec2.previous_id == exec1.id
    assert exec1.input_message_ids == [message.id for message in exec1.input_messages]
    assert exec1.step_ids == [step1.id, step2.id]


def test_reference_list_append_updates_underlying_id_lists() -> None:
    run = state.WorkflowExecution(workflow_name="wf")
    exec1 = run.create_node_execution(node="node", status=state.RunStatus.RUNNING)
    message = _make_message("input")
    step = state.Step(
        execution=exec1,
        type=state.StepType.OUTPUT_MESSAGE,
        message=_make_message("output"),
        workflow_execution=run,
    )
    run.messages_by_id[message.id] = message

    exec1.input_messages.append(message)
    exec1.steps.append(step)
    run.steps.append(step)

    assert exec1.input_message_ids == [message.id]
    assert exec1.step_ids == [step.id]
    assert run.step_ids == [step.id]


def test_reference_properties_are_read_only() -> None:
    run = state.WorkflowExecution(workflow_name="wf")
    exec1 = run.create_node_execution(node="node-1", status=state.RunStatus.RUNNING)
    exec2 = run.create_node_execution(node="node-2", status=state.RunStatus.RUNNING)
    message = _make_message("output")
    step = state.Step(
        execution=exec1,
        type=state.StepType.OUTPUT_MESSAGE,
        message=message,
        workflow_execution=run,
    )
    run.messages_by_id[message.id] = message
    run.steps_by_id[step.id] = step
    run.step_ids = [step.id]
    exec1.step_ids = [step.id]
    run.attach_runtime_refs()

    with pytest.raises(AttributeError):
        exec2.previous = exec1
    with pytest.raises(AttributeError):
        exec1.input_messages = [message]
    with pytest.raises(AttributeError):
        exec1.steps = [step]
    with pytest.raises(AttributeError):
        step.execution = exec2
    with pytest.raises(AttributeError):
        step.message = message
    with pytest.raises(AttributeError):
        run.steps = [step]
