from __future__ import annotations

from vocode import state as vocode_state
from vocode.runstate import tasklist as task_state


def test_tasklist_single_in_progress_validator() -> None:
    task_state.TaskList(
        todos=[
            task_state.Task(
                id="a",
                title="A",
                status=task_state.TaskStatus.pending,
            ),
            task_state.Task(
                id="b",
                title="B",
                status=task_state.TaskStatus.in_progress,
            ),
        ]
    )

    try:
        task_state.TaskList(
            todos=[
                task_state.Task(
                    id="a",
                    title="A",
                    status=task_state.TaskStatus.in_progress,
                ),
                task_state.Task(
                    id="b",
                    title="B",
                    status=task_state.TaskStatus.in_progress,
                ),
            ]
        )
        assert False, "Expected validation error for multiple in_progress tasks"
    except ValueError:
        pass


def test_tasklist_get_and_save_on_workflow_execution() -> None:
    execution = vocode_state.WorkflowExecution(workflow_name="test")
    empty = task_state.get_task_list(execution)
    assert isinstance(empty, task_state.TaskList)
    assert empty.todos == []

    tasks = task_state.TaskList(todos=[task_state.Task(id="t1", title="Task 1")])
    task_state.save_task_list(execution, tasks)

    loaded = task_state.get_task_list(execution)
    assert len(loaded.todos) == 1
    assert loaded.todos[0].id == "t1"
    assert loaded.todos[0].title == "Task 1"


def test_merge_tasks_replace_and_merge() -> None:
    existing = task_state.TaskList(
        todos=[
            task_state.Task(
                id="a",
                title="Old A",
                status=task_state.TaskStatus.pending,
            ),
            task_state.Task(
                id="b",
                title="Old B",
                status=task_state.TaskStatus.completed,
            ),
        ]
    )

    new = [
        task_state.Task(
            id="b",
            title="New B",
            status=task_state.TaskStatus.pending,
        ),
        task_state.Task(
            id="c",
            title="New C",
            status=task_state.TaskStatus.in_progress,
        ),
    ]

    replaced = task_state.merge_tasks(existing, new, merge=False)
    assert [t.id for t in replaced.todos] == ["b", "c"]

    merged = task_state.merge_tasks(existing, new, merge=True)
    assert [t.id for t in merged.todos] == ["b", "c", "a"]
    ids = {t.id: t for t in merged.todos}
    assert ids["b"].title == "New B"
    assert ids["c"].title == "New C"
    assert ids["a"].title == "Old A"
