from uuid import uuid4

from vocode import models, state
from vocode.history.manager import HistoryManager
from vocode.runner.base import iter_execution_messages


def _make_message(text: str = "msg") -> state.Message:
    return state.Message(role=models.Role.USER, text=text)


def _make_node_execution(
    run: state.WorkflowExecution, name: str
) -> state.NodeExecution:
    history = HistoryManager()
    message = _make_message()
    history.upsert_message(run, message)
    node_execution = state.NodeExecution(
        workflow_execution=run,
        node=name,
        input_message_ids=[message.id],
        status=state.RunStatus.RUNNING,
    )
    return history.upsert_node_execution(run, node_execution)


def test_delete_steps_removes_from_workflow_and_node_executions() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="test")
    exec1 = _make_node_execution(run, "node-1")
    exec2 = _make_node_execution(run, "node-2")

    step1 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )
    step2 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.INPUT_MESSAGE,
        ),
    )
    step3 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec2.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )

    history.delete_steps(run, [step1.id, step3.id])

    remaining_ids = {s.id for s in run.iter_steps()}
    assert remaining_ids == {step2.id}

    exec1_ids = {s.id for s in exec1.iter_steps()}
    exec2_ids = {s.id for s in exec2.iter_steps()}
    assert exec1_ids == {step2.id}
    assert exec2_ids == set()


def test_delete_steps_ignores_unknown_ids_and_empty_input() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="test")
    exec1 = _make_node_execution(run, "node-1")
    step1 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )

    unknown_id = uuid4()
    history.delete_steps(run, [unknown_id])

    assert [s.id for s in run.iter_steps()] == [step1.id]
    assert [s.id for s in exec1.iter_steps()] == [step1.id]

    history.delete_steps(run, [])

    assert [s.id for s in run.iter_steps()] == [step1.id]
    assert [s.id for s in exec1.iter_steps()] == [step1.id]


def test_delete_step_delegates_to_delete_steps() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="test")
    exec1 = _make_node_execution(run, "node-1")
    step1 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )

    history.delete_step(run, step1.id)

    assert tuple(run.iter_steps()) == ()
    assert tuple(exec1.iter_steps()) == ()


def test_step_is_final_defaults_false() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="test")
    exec1 = _make_node_execution(run, "node-1")
    step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )
    assert step.is_final is False


def test_iter_execution_messages_traverses_previous_chain_in_order() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="test")
    in_1a = state.Message(role=models.Role.USER, text="in-1-a")
    in_1b = state.Message(role=models.Role.USER, text="in-1-b")
    history.upsert_message(run, in_1a)
    history.upsert_message(run, in_1b)
    exec1 = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node",
            input_message_ids=[in_1a.id, in_1b.id],
            status=state.RunStatus.RUNNING,
        ),
    )

    out_1a = state.Message(role=models.Role.ASSISTANT, text="out-1-a")
    in_1c = state.Message(role=models.Role.USER, text="in-1-c")
    history.upsert_message(run, out_1a)
    history.upsert_message(run, in_1c)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=out_1a.id,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=in_1c.id,
        ),
    )

    in_2a = state.Message(role=models.Role.USER, text="in-2-a")
    out_2a = state.Message(role=models.Role.ASSISTANT, text="out-2-a")
    history.upsert_message(run, in_2a)
    history.upsert_message(run, out_2a)
    exec2 = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node",
            previous_id=exec1.id,
            input_message_ids=[in_2a.id],
            status=state.RunStatus.RUNNING,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec2.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=out_2a.id,
        ),
    )

    texts = [m.text for (m, _t) in iter_execution_messages(exec2)]
    assert texts == ["in-1-a", "in-1-b", "out-1-a", "in-1-c", "in-2-a", "out-2-a"]


def test_delete_node_execution_removes_execution_and_child_steps() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="test")
    exec1 = _make_node_execution(run, "node-1")
    exec2 = _make_node_execution(run, "node-2")

    step1 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )
    step2 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.INPUT_MESSAGE,
        ),
    )
    step3 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec2.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )

    history.delete_node_execution(run, exec1.id)

    assert exec1.id not in run.node_executions
    assert exec2.id in run.node_executions

    remaining_step_ids = {s.id for s in run.iter_steps()}
    assert remaining_step_ids == {step3.id}

    assert tuple(exec2.iter_steps()) == (step3,)
    assert all(step.id != step1.id for step in run.iter_steps())
    assert all(step.id != step2.id for step in run.iter_steps())


def test_reference_properties_reflect_canonical_ids() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    in_1a = state.Message(role=models.Role.USER, text="in-1-a")
    in_1b = state.Message(role=models.Role.USER, text="in-1-b")
    out_1a = state.Message(role=models.Role.ASSISTANT, text="out-1-a")
    in_1c = state.Message(role=models.Role.USER, text="in-1-c")
    in_2a = state.Message(role=models.Role.USER, text="in-2-a")
    out_2a = state.Message(role=models.Role.ASSISTANT, text="out-2-a")
    for message in [in_1a, in_1b, out_1a, in_1c, in_2a, out_2a]:
        history.upsert_message(run, message)

    exec1 = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node",
            input_message_ids=[in_1a.id, in_1b.id],
            status=state.RunStatus.RUNNING,
        ),
    )
    step1 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=out_1a.id,
        ),
    )
    step2 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=in_1c.id,
        ),
    )
    exec2 = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node",
            previous_id=exec1.id,
            input_message_ids=[in_2a.id],
            status=state.RunStatus.RUNNING,
        ),
    )
    step3 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec2.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=out_2a.id,
        ),
    )

    assert step1.execution_id == exec1.id
    assert step1.message_id == out_1a.id
    assert exec2.previous_id == exec1.id
    assert exec1.input_message_ids == [in_1a.id, in_1b.id]
    assert exec1.step_ids == [step1.id, step2.id]
    assert tuple(exec2.iter_steps()) == (step3,)


def test_workflow_execution_active_branch_defaults_to_linear_path() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    exec1 = _make_node_execution(run, "node-1")
    step1 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )
    step2 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.INPUT_MESSAGE,
        ),
    )

    active_steps = tuple(run.iter_steps())

    assert [step.id for step in active_steps] == [step1.id, step2.id]
    assert step2.parent is step1
    assert step1.children == (step2,)
    assert run.active_branch_id is not None
    branch = run.get_active_branch()
    assert branch.head_step_id == step2.id


def test_workflow_execution_step_navigation_helpers() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = _make_node_execution(run, "node-1")
    step1 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )
    step2 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
        ),
    )
    step3 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )

    assert run.get_step_ids() == [step1.id, step2.id, step3.id]
    assert run.get_steps() == (step1, step2, step3)
    assert run.get_step_id_set() == {step1.id, step2.id, step3.id}
    assert run.get_first_step() is step1
    assert run.get_last_step() is step3
    assert run.get_previous_step(step1.id) is None
    assert run.get_previous_step(step3.id) is step2
    assert run.get_next_step(step1.id) is step2
    assert run.get_next_step(step3.id) is None


def test_switch_branch_changes_active_projection() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    exec1 = _make_node_execution(run, "node-1")
    step1 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )
    step2 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec1.id,
            type=state.StepType.INPUT_MESSAGE,
        ),
    )
    branch1_id = run.get_active_branch().id

    branch2 = history.create_branch(
        run,
        head_step_id=step1.id,
        base_step_id=step2.id,
        activate=True,
    )
    exec2 = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node=exec1.node,
            status=state.RunStatus.RUNNING,
            branch_id=branch2.id,
            input_message_ids=list(exec1.input_message_ids),
            previous_id=exec1.previous_id,
        ),
    )
    step3 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=exec2.id,
            parent_step_id=step1.id,
            type=state.StepType.INPUT_MESSAGE,
        ),
    )

    assert [step.id for step in run.iter_steps()] == [step1.id, step3.id]
    assert run.step_ids == [step1.id, step3.id]
    assert exec1.step_ids == [step1.id, step2.id]
    assert exec2.step_ids == [step3.id]

    history.switch_branch(run, branch1_id)

    assert run.get_active_branch().id == branch1_id
    assert [step.id for step in run.iter_steps()] == [step1.id, step2.id]
    assert run.step_ids == [step1.id, step2.id]
    assert exec1.step_ids == [step1.id, step2.id]
    assert exec2.step_ids == [step3.id]
    assert branch2.head_step_id == step3.id


def test_history_manager_fork_from_step_creates_new_branch_head() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = _make_node_execution(run, "node-1")
    step1 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )
    step2 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
        ),
    )

    replacement = state.Step(
        workflow_execution=run,
        execution_id=execution.id,
        type=state.StepType.INPUT_MESSAGE,
    )
    result = history.fork_from_step(
        run,
        step1.id,
        replacement,
        base_step_id=step2.id,
    )

    assert result.changed is True
    assert result.created_branch_id is not None
    assert run.step_ids == [step1.id, replacement.id]
    assert execution.step_ids == [step1.id, step2.id]
    assert replacement.execution_id != execution.id
    assert replacement.execution.branch_id == result.created_branch_id
    assert replacement.execution.step_ids == [replacement.id]


def test_upsert_step_updates_existing_step_without_duplicate_node_step_ids() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = _make_node_execution(run, "node-1")
    message1 = state.Message(role=models.Role.ASSISTANT, text="one")
    message2 = state.Message(role=models.Role.ASSISTANT, text="two")
    history.upsert_message(run, message1)
    history.upsert_message(run, message2)

    step = history.upsert_step(
        run,
        state.Step(
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=message1.id,
        ),
    )

    updated = step.model_copy(update={"message_id": message2.id, "is_complete": False})
    history.upsert_step(run, updated)

    assert execution.step_ids == [step.id]
    assert run.get_step(step.id).message_id == message2.id
    assert run.get_step(step.id).is_complete is False


def test_upsert_step_reparents_existing_step() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = _make_node_execution(run, "node-1")
    parent1 = history.upsert_step(
        run,
        state.Step(
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )
    parent2 = history.upsert_step(
        run,
        state.Step(
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )
    child = history.upsert_step(
        run,
        state.Step(
            execution_id=execution.id,
            parent_step_id=parent1.id,
            type=state.StepType.INPUT_MESSAGE,
        ),
    )

    history.upsert_step(
        run,
        child.model_copy(update={"parent_step_id": parent2.id}),
    )

    assert child.id not in run.get_step(parent1.id).child_step_ids
    assert child.id in run.get_step(parent2.id).child_step_ids


def test_upsert_step_moves_existing_step_between_node_executions() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution1 = _make_node_execution(run, "node-1")
    execution2 = _make_node_execution(run, "node-2")
    step = history.upsert_step(
        run,
        state.Step(
            execution_id=execution1.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )

    history.upsert_step(
        run,
        step.model_copy(update={"execution_id": execution2.id}),
    )

    assert step.id not in execution1.step_ids
    assert step.id in execution2.step_ids
