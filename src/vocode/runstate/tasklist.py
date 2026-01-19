from __future__ import annotations

from enum import Enum
from typing import List, TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

if TYPE_CHECKING:
    from vocode.state import WorkflowExecution


class TaskStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"


class Task(BaseModel):
    id: str = Field(
        ...,
        description="Stable identifier for this task (e.g. 'step-1').",
    )
    title: str = Field(
        ...,
        description="Short human-readable description of this task.",
    )
    status: TaskStatus = Field(
        default=TaskStatus.pending,
        description="Current status of this task.",
    )


class TaskList(BaseModel):
    todos: List[Task] = Field(
        default_factory=list,
        description="Current ordered list of tasks for this run.",
    )

    @model_validator(mode="after")
    def _validate_single_in_progress(self) -> "TaskList":
        in_progress_count = sum(
            1 for task in self.todos if task.status == TaskStatus.in_progress
        )
        if in_progress_count > 1:
            raise ValueError(
                "At most one task may have status 'in_progress' in the task list."
            )
        return self


_STATE_KEY = "task_list"


def get_task_list(execution: "WorkflowExecution") -> TaskList:
    raw = execution.state.get(_STATE_KEY)
    if isinstance(raw, dict):
        try:
            return TaskList.model_validate(raw)
        except Exception:
            return TaskList()
    return TaskList()


def save_task_list(execution: "WorkflowExecution", task_list: TaskList) -> None:
    execution.state[_STATE_KEY] = task_list.model_dump()


def merge_tasks(existing: TaskList, new_tasks: List[Task], merge: bool) -> TaskList:
    if not merge:
        return TaskList(todos=list(new_tasks))

    existing_by_id = {task.id: task for task in existing.todos}
    merged_todos: List[Task] = []

    for task in new_tasks:
        existing_task = existing_by_id.pop(task.id, None)
        if existing_task is not None:
            merged_todos.append(Task(id=task.id, title=task.title, status=task.status))
        else:
            merged_todos.append(task)

    for remaining in existing.todos:
        if any(t.id == remaining.id for t in merged_todos):
            continue
        merged_todos.append(remaining)

    return TaskList(todos=merged_todos)
