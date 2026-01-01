from uuid import uuid4

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

    exec1.steps = [step1, step2]
    exec2.steps = [step3]

    run = state.WorkflowExecution(
        workflow_name="test",
        node_executions={exec1.id: exec1, exec2.id: exec2},
        steps=[step1, step2, step3],
    )

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
    exec1.steps = [step1]

    run = state.WorkflowExecution(
        workflow_name="test",
        node_executions={exec1.id: exec1},
        steps=[step1],
    )

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
    exec1.steps = [step1]

    run = state.WorkflowExecution(
        workflow_name="test",
        node_executions={exec1.id: exec1},
        steps=[step1],
    )

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
    exec1.steps = [step1, step2]

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
    exec2.steps = [step3]

    texts = [m.text for (m, _t) in iter_execution_messages(exec2)]
    assert texts == ["in-1-a", "in-1-b", "out-1-a", "in-1-c", "in-2-a", "out-2-a"]


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
    exec1.steps = [step1, step2]

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
    exec2.steps = [step3]

    texts = [m.text for (m, _t) in iter_execution_messages(exec2)]
    assert texts == ["in-1-a", "in-1-b", "out-1-a", "in-1-c", "in-2-a", "out-2-a"]
