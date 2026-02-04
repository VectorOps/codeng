from __future__ import annotations

import io
import json

from rich import console as rich_console

from vocode import settings as vocode_settings
from vocode.tui import tcf as tui_tcf
from vocode.tui.tcf import task_tool as task_tcf
from vocode.tui.lib import terminal as tui_terminal


def test_task_tool_formatter_renders_tasks_from_text_payload() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    term = tui_terminal.Terminal(console=console)

    formatter = task_tcf.TaskToolFormatter()
    payload = {
        "todos": [
            {"id": "a", "title": "Task A", "status": "pending"},
            {"id": "b", "title": "Task B", "status": "in_progress"},
            {"id": "c", "title": "Task C", "status": "completed"},
        ]
    }
    result = json.dumps(payload)
    rendered = formatter.format_output(
        terminal=term,
        tool_name="update_plan",
        result=result,
        config=vocode_settings.ToolCallFormatter(title="Plan", formatter="tasklist"),
    )

    assert rendered is not None
    console.print(rendered)
    output = buffer.getvalue()
    assert "Plan:" in output
    assert "[ ] Task A" in output
    assert "[>] Task B" in output
    assert "[x] Task C" in output


def test_task_tool_formatter_disables_execution_stats() -> None:
    buffer = io.StringIO()
    console = rich_console.Console(file=buffer, force_terminal=True, color_system=None)
    term = tui_terminal.Terminal(console=console)

    formatter = task_tcf.TaskToolFormatter()
    payload = {
        "todos": [
            {"id": "a", "title": "Task A", "status": "pending"},
        ]
    }
    result = json.dumps(payload)
    rendered = formatter.format_output(
        terminal=term,
        tool_name="update_plan",
        result=result,
        config=vocode_settings.ToolCallFormatter(
            title="Plan",
            formatter="tasklist",
            show_execution_stats=False,
        ),
    )
    assert rendered is not None
