from __future__ import annotations

import json
from typing import Any

from vocode import settings as vocode_settings
from vocode import state as vocode_state
from vocode.tools import base as tools_base

from .base import command, option


USAGE = (
    "Debug commands:\n\n"
    "  /debug help\n"
    "    Show this help message with examples.\n\n"
    "Knowledge base (know) commands:\n\n"
    "  /debug know search <query>\n"
    "    Full-text search across indexed documents.\n\n"
    "  /debug know summary <path> [more_paths...]\n"
    "    Summarize one or more documents by path.\n\n"
    "  /debug know list <glob-pattern>\n"
    "    List documents matching a glob pattern (e.g. *.md).\n"
)


async def _run_know_tool(server, tool_name: str, payload: dict[str, Any]) -> None:
    project = server.manager.project
    tools = project.tools or {}
    tool = tools.get(tool_name)
    if tool is None:
        await server.send_text_message(f"Know tool '{tool_name}' is not available.")
        return

    try:
        spec = vocode_settings.ToolSpec(name=tool_name)
    except Exception as exc:
        await server.send_text_message(f"Failed to construct ToolSpec: {exc}")
        return

    try:
        execution = vocode_state.WorkflowExecution(workflow_name="debug")
        tool_req = tools_base.ToolReq(execution=execution, spec=spec)
        resp = await tool.run(tool_req, payload)
    except Exception as exc:
        await server.send_text_message(f"Know tool '{tool_name}' raised: {exc}")
        return

    if not resp or resp.text is None:
        await server.send_text_message("(no output)")
        return

    text = resp.text
    formatted = text
    try:
        obj = json.loads(text)
    except Exception:
        pass
    else:
        formatted = json.dumps(obj, indent=2, sort_keys=True)

    await server.send_text_message(formatted)


@command(
    "debug",
    description="Debug and inspection commands",
    params=["domain", "action", "args..."],
)
@option(0, "args", type=str, splat=True)
async def _debug(server, args: list[str]) -> None:
    if not args or args[0] == "help":
        await server.send_text_message(USAGE)
        return

    domain = args[0]

    if domain != "know":
        await server.send_text_message(USAGE)
        return

    if len(args) < 2:
        await server.send_text_message("Usage: /debug know <search|summary|list> ...")
        return

    action = args[1]
    sub_args = args[2:]

    if action == "search":
        if not sub_args:
            await server.send_text_message("Usage: /debug know search <query>")
            return
        query = " ".join(sub_args)
        await _run_know_tool(
            server,
            "search_project",
            {
                "query": query,
            },
        )
        return

    if action in {"summary", "summarize"}:
        if not sub_args:
            await server.send_text_message(
                "Usage: /debug know summary <path> [more_paths...]"
            )
            return
        await _run_know_tool(
            server,
            "summarize_files",
            {
                "paths": sub_args,
            },
        )
        return

    if action in {"list", "files"}:
        if not sub_args:
            await server.send_text_message("Usage: /debug know list <glob-pattern>")
            return
        pattern = sub_args[0]
        await _run_know_tool(
            server,
            "list_files",
            {
                "pattern": pattern,
            },
        )
        return

    await server.send_text_message(USAGE)
