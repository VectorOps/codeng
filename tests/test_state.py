from uuid import uuid4

from vocode import models, state
from vocode.runner.base import iter_execution_messages


def _make_message(text: str = "msg") -> state.Message:
    return state.Message(role=models.Role.USER, text=text)


def _make_node_execution(
    run: state.WorkflowExecution, name: str
) -> state.NodeExecution:
    message = _make_message()
    run.add_message(message)
    return run.create_node_execution(
        node=name,
        input_message_ids=[message.id],
        status=state.RunStatus.RUNNING,
    )


def test_delete_steps_removes_from_workflow_and_node_executions() -> None:
    run = state.WorkflowExecution(workflow_name="test")
    exec1 = _make_node_execution(run, "node-1")
    exec2 = _make_node_execution(run, "node-2")

    step1 = run.create_step(execution_id=exec1.id, type=state.StepType.OUTPUT_MESSAGE)
    step2 = run.create_step(execution_id=exec1.id, type=state.StepType.INPUT_MESSAGE)
    step3 = run.create_step(execution_id=exec2.id, type=state.StepType.OUTPUT_MESSAGE)

    run.delete_steps([step1.id, step3.id])

    remaining_ids = {s.id for s in run.iter_steps()}
    assert remaining_ids == {step2.id}

    exec1_ids = {s.id for s in exec1.iter_steps()}
    exec2_ids = {s.id for s in exec2.iter_steps()}
    assert exec1_ids == {step2.id}
    assert exec2_ids == set()


def test_delete_steps_ignores_unknown_ids_and_empty_input() -> None:
    run = state.WorkflowExecution(workflow_name="test")
    exec1 = _make_node_execution(run, "node-1")
    step1 = run.create_step(execution_id=exec1.id, type=state.StepType.OUTPUT_MESSAGE)

    unknown_id = uuid4()
    run.delete_steps([unknown_id])

    assert [s.id for s in run.iter_steps()] == [step1.id]
    assert [s.id for s in exec1.iter_steps()] == [step1.id]

    run.delete_steps([])

    assert [s.id for s in run.iter_steps()] == [step1.id]
    assert [s.id for s in exec1.iter_steps()] == [step1.id]


def test_delete_step_delegates_to_delete_steps() -> None:
    run = state.WorkflowExecution(workflow_name="test")
    exec1 = _make_node_execution(run, "node-1")
    step1 = run.create_step(execution_id=exec1.id, type=state.StepType.OUTPUT_MESSAGE)

    run.delete_step(step1.id)

    assert tuple(run.iter_steps()) == ()
    assert tuple(exec1.iter_steps()) == ()


def test_step_is_final_defaults_false() -> None:
    run = state.WorkflowExecution(workflow_name="test")
    exec1 = _make_node_execution(run, "node-1")
    step = run.create_step(execution_id=exec1.id, type=state.StepType.OUTPUT_MESSAGE)
    assert step.is_final is False


def test_iter_execution_messages_traverses_previous_chain_in_order() -> None:
    run = state.WorkflowExecution(workflow_name="test")
    in_1a = state.Message(role=models.Role.USER, text="in-1-a")
    in_1b = state.Message(role=models.Role.USER, text="in-1-b")
    run.add_message(in_1a)
    run.add_message(in_1b)
    exec1 = run.create_node_execution(
        node="node",
        input_message_ids=[in_1a.id, in_1b.id],
        status=state.RunStatus.RUNNING,
    )

    out_1a = state.Message(role=models.Role.ASSISTANT, text="out-1-a")
    in_1c = state.Message(role=models.Role.USER, text="in-1-c")
    run.add_message(out_1a)
    run.add_message(in_1c)
    run.create_step(
        execution_id=exec1.id,
        type=state.StepType.OUTPUT_MESSAGE,
        message_id=out_1a.id,
    )
    run.create_step(
        execution_id=exec1.id,
        type=state.StepType.INPUT_MESSAGE,
        message_id=in_1c.id,
    )

    in_2a = state.Message(role=models.Role.USER, text="in-2-a")
    out_2a = state.Message(role=models.Role.ASSISTANT, text="out-2-a")
    run.add_message(in_2a)
    run.add_message(out_2a)
    exec2 = run.create_node_execution(
        node="node",
        previous_id=exec1.id,
        input_message_ids=[in_2a.id],
        status=state.RunStatus.RUNNING,
    )
    run.create_step(
        execution_id=exec2.id,
        type=state.StepType.OUTPUT_MESSAGE,
        message_id=out_2a.id,
    )

    texts = [m.text for (m, _t) in iter_execution_messages(exec2)]
    assert texts == ["in-1-a", "in-1-b", "out-1-a", "in-1-c", "in-2-a", "out-2-a"]


def test_delete_node_execution_removes_execution_and_child_steps() -> None:
    run = state.WorkflowExecution(workflow_name="test")
    exec1 = _make_node_execution(run, "node-1")
    exec2 = _make_node_execution(run, "node-2")

    step1 = run.create_step(execution_id=exec1.id, type=state.StepType.OUTPUT_MESSAGE)
    step2 = run.create_step(execution_id=exec1.id, type=state.StepType.INPUT_MESSAGE)
    step3 = run.create_step(execution_id=exec2.id, type=state.StepType.OUTPUT_MESSAGE)

    run.delete_node_execution(exec1.id)

    assert exec1.id not in run.node_executions
    assert exec2.id in run.node_executions

    remaining_step_ids = {s.id for s in run.iter_steps()}
    assert remaining_step_ids == {step3.id}

    assert tuple(exec2.iter_steps()) == (step3,)
    assert all(step.id != step1.id for step in run.iter_steps())
    assert all(step.id != step2.id for step in run.iter_steps())


def test_reference_properties_reflect_canonical_ids() -> None:
    run = state.WorkflowExecution(workflow_name="wf")
    in_1a = state.Message(role=models.Role.USER, text="in-1-a")
    in_1b = state.Message(role=models.Role.USER, text="in-1-b")
    out_1a = state.Message(role=models.Role.ASSISTANT, text="out-1-a")
    in_1c = state.Message(role=models.Role.USER, text="in-1-c")
    in_2a = state.Message(role=models.Role.USER, text="in-2-a")
    out_2a = state.Message(role=models.Role.ASSISTANT, text="out-2-a")
    for message in [in_1a, in_1b, out_1a, in_1c, in_2a, out_2a]:
        run.add_message(message)

    exec1 = run.create_node_execution(
        node="node",
        input_message_ids=[in_1a.id, in_1b.id],
        status=state.RunStatus.RUNNING,
    )
    step1 = run.create_step(
        execution_id=exec1.id,
        type=state.StepType.OUTPUT_MESSAGE,
        message_id=out_1a.id,
    )
    step2 = run.create_step(
        execution_id=exec1.id,
        type=state.StepType.INPUT_MESSAGE,
        message_id=in_1c.id,
    )
    exec2 = run.create_node_execution(
        node="node",
        previous_id=exec1.id,
        input_message_ids=[in_2a.id],
        status=state.RunStatus.RUNNING,
    )
    step3 = run.create_step(
        execution_id=exec2.id,
        type=state.StepType.OUTPUT_MESSAGE,
        message_id=out_2a.id,
    )

    assert step1.execution_id == exec1.id
    assert step1.message_id == out_1a.id
    assert exec2.previous_id == exec1.id
    assert exec1.input_message_ids == [in_1a.id, in_1b.id]
    assert exec1.step_ids == [step1.id, step2.id]
    assert tuple(exec2.iter_steps()) == (step3,)


def test_workflow_execution_active_branch_defaults_to_linear_path() -> None:
    run = state.WorkflowExecution(workflow_name="wf")
    exec1 = _make_node_execution(run, "node-1")
    step1 = run.create_step(execution_id=exec1.id, type=state.StepType.OUTPUT_MESSAGE)
    step2 = run.create_step(execution_id=exec1.id, type=state.StepType.INPUT_MESSAGE)

    active_steps = tuple(run.iter_steps())

    assert [step.id for step in active_steps] == [step1.id, step2.id]
    assert step2.parent is step1
    assert step1.children == (step2,)
    assert run.active_branch_id is not None
    branch = run.get_active_branch()
    assert branch.head_step_id == step2.id


def test_switch_branch_changes_active_projection() -> None:
    run = state.WorkflowExecution(workflow_name="wf")
    exec1 = _make_node_execution(run, "node-1")
    step1 = run.create_step(execution_id=exec1.id, type=state.StepType.OUTPUT_MESSAGE)
    step2 = run.create_step(execution_id=exec1.id, type=state.StepType.INPUT_MESSAGE)
    branch1_id = run.get_active_branch().id

    branch2 = run.create_branch(
        head_step_id=step1.id,
        base_step_id=step2.id,
        activate=True,
    )
    step3 = run.create_step(
        execution_id=exec1.id,
        parent_step_id=step1.id,
        type=state.StepType.INPUT_MESSAGE,
    )

    assert [step.id for step in run.iter_steps()] == [step1.id, step3.id]

    run.switch_branch(branch1_id)

    assert run.get_active_branch().id == branch1_id
    assert [step.id for step in run.iter_steps()] == [step1.id, step2.id]
    assert branch2.head_step_id == step3.id
