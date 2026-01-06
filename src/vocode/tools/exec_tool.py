from __future__ import annotations

import asyncio
import json
import platform
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from typing import Callable
from vocode.tools.base import BaseTool, ToolTextResponse, ToolFactory
from vocode.settings import EXEC_TOOL_MAX_OUTPUT_CHARS_DEFAULT, ToolSpec
from vocode.proc.shell import ShellManager

if TYPE_CHECKING:
    from vocode.project import Project

# Default timeout for exec tool invocations (seconds).
# Can be overridden per-tool via ToolSpec.config["timeout_s"].
EXEC_TOOL_TIMEOUT_S: float = 60.0


def _get_max_output_chars(project: "Project", spec: ToolSpec) -> int:
    """Determine max output size for this exec tool invocation.

    Priority:
    1) Per-tool override via ToolSpec.config["max_output_chars"] if provided and valid.
    2) Project-level Settings.exec_tool.max_output_chars if configured.
    3) Repository default constant.
    """

    # 1) Per-tool override
    cfg = spec.config or {}
    max_chars_cfg = cfg.get("max_output_chars")
    if isinstance(max_chars_cfg, (int, float)) and max_chars_cfg > 0:
        return int(max_chars_cfg)

    # 2) Project-level setting
    settings = project.settings
    if (
        settings is not None
        and settings.tool_settings is not None
        and settings.tool_settings.exec_tool is not None
    ):
        try:
            max_chars = int(settings.tool_settings.exec_tool.max_output_chars)
            if max_chars > 0:
                return max_chars
        except (TypeError, ValueError):  # pragma: no cover - defensive
            pass

    # 3) Fallback to default
    return EXEC_TOOL_MAX_OUTPUT_CHARS_DEFAULT


@ToolFactory.register("exec")
class ExecTool(BaseTool):
    """
    Execute a command via the project's ShellManager.
    Collects combined stdout/stderr, enforces a per-call timeout, and returns a JSON string payload.
    """

    name = "exec"

    async def run(self, spec: ToolSpec, args: Any):
        if not isinstance(spec, ToolSpec):
            raise TypeError("ExecTool requires a resolved ToolSpec")
        if self.prj.shells is None:
            raise RuntimeError("ExecTool requires project.shells (ShellManager)")

        shell_manager = self.prj.shells

        # Parse args
        command: Optional[str] = None
        if isinstance(args, str):
            command = args
        elif isinstance(args, dict):
            arg_cmd = args.get("command")
            if isinstance(arg_cmd, str):
                command = arg_cmd
        if not command:
            raise ValueError("ExecTool requires 'command' (string) argument")

        # Determine timeout: allow override via tool spec config, then
        # fall back to project-level settings, then constant default.
        cfg = spec.config or {}
        timeout_s: float
        raw_timeout = cfg.get("timeout_s")
        if raw_timeout is not None:
            try:
                timeout_s = float(raw_timeout)
            except (TypeError, ValueError):
                timeout_s = EXEC_TOOL_TIMEOUT_S
        else:
            settings = self.prj.settings
            if (
                settings is not None
                and settings.tool_settings is not None
                and settings.tool_settings.exec_tool is not None
                and settings.tool_settings.exec_tool.timeout_s is not None
            ):
                try:
                    timeout_s = float(settings.exec_tool.timeout_s)
                except (TypeError, ValueError):  # pragma: no cover - defensive
                    timeout_s = EXEC_TOOL_TIMEOUT_S
            else:
                timeout_s = EXEC_TOOL_TIMEOUT_S

        handle = await shell_manager.run(command, timeout=timeout_s)

        stdout_parts: List[str] = []
        stderr_parts: List[str] = []

        async def _read_stdout():
            async for chunk in handle.iter_stdout():
                stdout_parts.append(chunk)

        async def _read_stderr():
            async for chunk in handle.iter_stderr():
                stderr_parts.append(chunk)

        readers = [
            asyncio.create_task(_read_stdout()),
            asyncio.create_task(_read_stderr()),
        ]

        timed_out = False
        rc: Optional[int] = None
        try:
            rc = await handle.wait()
        except asyncio.TimeoutError:
            timed_out = True
            rc = None
        finally:
            await asyncio.gather(*readers, return_exceptions=True)

        output = "".join(stdout_parts) + "".join(stderr_parts)
        max_output_chars = _get_max_output_chars(self.prj, spec)
        if len(output) > max_output_chars:
            output = output[:max_output_chars]
        payload = {
            "output": output,
            "exit_code": rc,
            "timed_out": timed_out,
        }
        return ToolTextResponse(text=json.dumps(payload))

    async def openapi_spec(self, spec: ToolSpec) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Execute a shell command and return combined stdout/stderr, exit code, and timeout status. "
                f"Timeout is configurable via tool config (timeout_s) and defaults to {EXEC_TOOL_TIMEOUT_S} seconds. "
                "Output is truncated to ~10KB."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Command to run (executed via system shell).",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        }


# registered via ToolFactory
