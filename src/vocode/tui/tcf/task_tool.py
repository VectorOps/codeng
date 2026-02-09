from __future__ import annotations

import json
from typing import Any, Dict, List

from rich import text as rich_text

from vocode import settings as vocode_settings
from vocode.tui import styles as tui_styles
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import terminal as tui_terminal


_STATUS_PENDING = "pending"
_STATUS_IN_PROGRESS = "in_progress"
_STATUS_COMPLETED = "completed"


@tui_tcf.ToolCallFormatterManager.register("update_plan")
class TaskToolFormatter(tui_tcf.BaseToolCallFormatter):
    show_execution_stats_default: bool = False

    def format_input(
        self,
        terminal: tui_terminal.Terminal,
        tool_name: str,
        arguments: Any,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        return None

    def format_output(
        self,
        terminal: tui_terminal.Terminal,
        tool_name: str,
        result: Any,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        title = self.format_tool_name(tool_name)
        if config is not None and config.title:
            title = config.title

        tasks = self._parse_tasks_from_result(result)
        if tasks:
            return self._format_tasks(tasks, title)

        payload: str
        if isinstance(result, str):
            payload = result
        else:
            try:
                payload = json.dumps(result, ensure_ascii=False)
            except Exception:
                payload = str(result)

        text = rich_text.Text(no_wrap=False)
        text.append(f"{title}:", style=tui_styles.TOOL_CALL_NAME_STYLE)
        text.append("\n")
        text.append(payload)
        return text

    @staticmethod
    def _parse_tasks_sequence(raw: Any) -> List[Dict[str, str]]:
        tasks: List[Dict[str, str]] = []
        if not isinstance(raw, list):
            return tasks

        for item in raw:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("id")
            raw_title = item.get("title")
            raw_status = item.get("status")

            task_id = str(raw_id) if raw_id is not None else ""
            title = str(raw_title or raw_id or "")
            status = str(raw_status or _STATUS_PENDING)

            tasks.append(
                {
                    "id": task_id,
                    "title": title,
                    "status": status,
                }
            )
        return tasks

    def _parse_tasks_from_result(self, result: Any) -> List[Dict[str, str]]:
        if isinstance(result, dict):
            if "todos" in result:
                return self._parse_tasks_sequence(result.get("todos"))
            text = result.get("text")
            if isinstance(text, str):
                try:
                    payload = json.loads(text)
                except Exception:
                    return []
                if isinstance(payload, dict) and "todos" in payload:
                    return self._parse_tasks_sequence(payload.get("todos"))
                return []

        if isinstance(result, str):
            try:
                payload = json.loads(result)
            except Exception:
                return []
            if isinstance(payload, dict) and "todos" in payload:
                return self._parse_tasks_sequence(payload.get("todos"))
        return []

    @staticmethod
    def _status_prefix(status: str) -> str:
        if status == _STATUS_COMPLETED:
            return "[x]"
        if status == _STATUS_IN_PROGRESS:
            return "[>]"
        return "[ ]"

    def _format_tasks(
        self,
        tasks: List[Dict[str, str]],
        title: str,
    ) -> tui_base.Renderable:
        text = rich_text.Text(no_wrap=False)
        text.append(f"{title}:", style=tui_styles.TOOL_CALL_NAME_STYLE)
        text.append("\n")

        for task in tasks:
            prefix = self._status_prefix(task.get("status", ""))
            label = task.get("title", "")
            line = f"{prefix} {label}"
            text.append(line)
            text.append("\n")

        return text
