from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from vocode.runstate import tasklist as task_state
from vocode.tools import base as tools_base
from vocode.settings import ToolSpec


@tools_base.ToolFactory.register("update_plan")
class UpdatePlanTool(tools_base.BaseTool):
    """Manage the current coding task plan for this workflow execution."""

    name = "update_plan"

    async def run(self, req: tools_base.ToolReq, args: Any):
        if not isinstance(args, dict):
            raise TypeError("update_plan expects arguments as an object")

        merge_flag = bool(args.get("merge", True))
        raw_todos = args.get("todos")

        if not isinstance(raw_todos, list) or not raw_todos:
            raise ValueError("update_plan requires a non-empty 'todos' list")

        current = task_state.get_task_list(req.execution)

        todos: List[task_state.Task] = []
        for item in raw_todos:
            if not isinstance(item, dict):
                raise TypeError(
                    "Each todo must be an object with id, status, and optional title"
                )

            raw_id = item.get("id")
            if not isinstance(raw_id, str) or not raw_id:
                raise ValueError("Each todo must provide a non-empty 'id' string")

            raw_status = item.get("status")
            if raw_status is None:
                raise ValueError("Each todo must provide a 'status'")
            try:
                status = task_state.TaskStatus(raw_status)
            except ValueError as exc:  # pragma: no cover - defensive
                raise ValueError(
                    "Invalid status; must be one of: "
                    f"{task_state.TaskStatus.pending.value}, "
                    f"{task_state.TaskStatus.in_progress.value}, "
                    f"{task_state.TaskStatus.completed.value}"
                ) from exc

            title = item.get("title")

            if merge_flag:
                if title is None:
                    existing_task: Optional[task_state.Task] = next(
                        (t for t in current.todos if t.id == raw_id),
                        None,
                    )
                    if existing_task is None:
                        raise ValueError(
                            "Title is required when adding a new task id during merge "
                            f"(missing title for id='{raw_id}')."
                        )
                    title = existing_task.title
            else:
                if not isinstance(title, str) or not title:
                    raise ValueError(
                        "Title is required for all tasks when merge is false "
                        f"(missing or empty title for id='{raw_id}')."
                    )

            todos.append(task_state.Task(id=raw_id, title=title, status=status))

        updated = task_state.merge_tasks(current, todos, merge_flag)

        in_progress_count = sum(
            1
            for task in updated.todos
            if task.status == task_state.TaskStatus.in_progress
        )
        if in_progress_count > 1:
            raise ValueError(
                "Only one task can have status 'in_progress' at a time in the task plan."
            )

        task_state.save_task_list(req.execution, updated)

        payload = {
            "todos": [
                {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status.value,
                }
                for task in updated.todos
            ]
        }

        return tools_base.ToolTextResponse(text=json.dumps(payload))

    async def openapi_spec(self, spec: ToolSpec) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Update or replace the current task plan for this coding session. "
                "Use stable ids (e.g. 'step-1') so you can update task status over time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "merge": {
                        "type": "boolean",
                        "description": (
                            "If true, merge these todos into the existing plan "
                            "(updating tasks by id and appending new ones). "
                            "If false, replace the existing plan entirely."
                        ),
                        "default": True,
                    },
                    "todos": {
                        "type": "array",
                        "description": (
                            "Ordered list of tasks representing the plan. "
                            "Each task must have a stable id, title, and status."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": (
                                        "Stable identifier for the task "
                                        "(e.g. 'step-1')."
                                    ),
                                },
                                "title": {
                                    "type": "string",
                                    "description": (
                                        "Short description of the task. "
                                        "Optional for merge requests; when omitted, "
                                        "only the status is updated for an existing task."
                                    ),
                                },
                                "status": {
                                    "type": "string",
                                    "description": (
                                        "Current status of this task. "
                                        "Must be one of: pending, in_progress, completed."
                                    ),
                                    "enum": [
                                        task_state.TaskStatus.pending.value,
                                        task_state.TaskStatus.in_progress.value,
                                        task_state.TaskStatus.completed.value,
                                    ],
                                },
                            },
                            "required": ["id", "status"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["todos"],
                "additionalProperties": False,
            },
        }
