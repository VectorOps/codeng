from __future__ import annotations

import json
from typing import Any, Dict, List

from rich import text as rich_text
from rich import console as rich_console

from vocode import settings as vocode_settings
from vocode.tui import styles as tui_styles
from vocode.tui import tcf as tui_tcf
from vocode.tui.lib import base as tui_base
from vocode.tui.lib import terminal as tui_terminal
from vocode.tui.tcf import render_utils as tcf_render_utils


_STATUS_PENDING = "pending"
_STATUS_IN_PROGRESS = "in_progress"
_STATUS_COMPLETED = "completed"


@tui_tcf.ToolCallFormatterManager.register("update_plan")
class TaskToolFormatter(tui_tcf.BaseToolCallFormatter):
    show_execution_stats_default: bool = False

    def render(
        self,
        terminal: tui_terminal.Terminal,
        req: Any,
        resp: Any,
        context: tui_tcf.ToolCallRenderContext,
        config: vocode_settings.ToolCallFormatter | None,
    ) -> tui_base.Renderable | None:
        tool_name = "update_plan"
        result: Any = None
        if req is not None:
            tool_name = req.name
        if resp is not None:
            tool_name = resp.name
            result = resp.result
        title = self.format_tool_name(tool_name)
        if config is not None and config.title:
            title = config.title

        tasks = self._parse_tasks_from_result(result)
        if tasks:
            return self._format_tasks(tasks, title, terminal, context)

        if req is not None and result is None:
            return tcf_render_utils.build_tool_line(
                terminal,
                title,
                context=context,
                prefix_icon=context.status_icon,
            )

        payload: str
        if isinstance(result, str):
            payload = result
        else:
            try:
                payload = json.dumps(result, ensure_ascii=False)
            except Exception:
                payload = str(result)

        line = tcf_render_utils.build_tool_line(
            terminal,
            title,
            context=context,
            prefix_icon=context.status_icon,
        )
        body = rich_text.Text(payload, no_wrap=False)
        return rich_console.Group(line, body)

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
        terminal: tui_terminal.Terminal,
        context: tui_tcf.ToolCallRenderContext,
    ) -> tui_base.Renderable:
        text = rich_text.Text(no_wrap=False)
        line = tcf_render_utils.build_tool_line(
            terminal,
            title,
            context=context,
            prefix_icon=context.status_icon,
        )
        text.append_text(line)
        text.append("\n")

        for task in tasks:
            prefix = self._status_prefix(task.get("status", ""))
            label = task.get("title", "")
            line = f"{prefix} {label}"
            text.append(line)
            text.append("\n")

        return text
