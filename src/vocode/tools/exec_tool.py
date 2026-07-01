from __future__ import annotations

import asyncio
import contextlib
import json
import platform
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from typing import Callable

from vocode.tools import base as tools_base
from vocode.settings import EXEC_TOOL_MAX_OUTPUT_CHARS_DEFAULT, ToolSpec
from vocode.proc.shell import ShellManager

if TYPE_CHECKING:
    from vocode.project import Project

# Default timeout for exec tool invocations (seconds).
# Can be overridden per-tool via ToolSpec.config["timeout_s"].
EXEC_TOOL_TIMEOUT_S: float = 60.0


class _OutputAccumulator:
    def __init__(self, max_chars: int) -> None:
        self._max_chars = max_chars
        self._truncated = False
        self._full_parts: List[str] = []
        self._full_len = 0
        self._head = ""
        self._tail = ""
        self._separator = "\n...\n"
        if max_chars > len(self._separator):
            keep = max_chars - len(self._separator)
            self._head_limit = keep // 2
            self._tail_limit = keep - self._head_limit
        else:
            self._head_limit = max_chars
            self._tail_limit = 0

    def add(self, chunk: str) -> None:
        if not chunk:
            return
        if self._max_chars <= 0:
            self._truncated = True
            return
        if not self._truncated:
            if self._full_len + len(chunk) <= self._max_chars:
                self._full_parts.append(chunk)
                self._full_len += len(chunk)
                return
            self._truncated = True
            full = "".join(self._full_parts)
            combined = full + chunk
            self._head = combined[: self._head_limit]
            remainder = combined[self._head_limit :]
            if self._tail_limit > 0:
                self._tail = remainder[-self._tail_limit :]
            self._full_parts = []
            self._full_len = 0
            return
        if self._tail_limit > 0:
            self._tail = (self._tail + chunk)[-self._tail_limit :]

    def render(self) -> str:
        if not self._truncated:
            return "".join(self._full_parts)
        if self._tail_limit <= 0:
            return self._head[: self._max_chars]
        return self._head + self._separator + self._tail


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


@tools_base.ToolFactory.register("exec")
class ExecTool(tools_base.BaseTool):
    """
    Execute a command via the project's ShellManager.
    Collects combined stdout/stderr, enforces a per-call timeout, and returns a JSON string payload.
    """

    name = "exec"

    async def run(self, req: tools_base.ToolReq, args: Any):
        spec = req.spec
        if self.prj.shells is None:
            raise RuntimeError("ExecTool requires project.shells (ShellManager)")

        shell_manager = self.prj.shells

        # Parse args
        command: Optional[str] = None
        arg_timeout: Optional[float] = None
        if isinstance(args, str):
            command = args
        elif isinstance(args, dict):
            arg_cmd = args.get("command")
            if isinstance(arg_cmd, str):
                command = arg_cmd
            raw_arg_timeout = args.get("timeout_s")
            if raw_arg_timeout is not None:
                try:
                    arg_timeout = float(raw_arg_timeout)
                except (TypeError, ValueError):
                    arg_timeout = None
        if not command:
            raise ValueError("ExecTool requires 'command' (string) argument")

        # Determine timeout: allow override via tool args, then tool spec
        # config, then fall back to project-level settings, then constant
        # default.
        cfg = spec.config or {}
        timeout_s: float
        if arg_timeout is not None:
            timeout_s = arg_timeout
        else:
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
                        timeout_s = float(settings.tool_settings.exec_tool.timeout_s)
                    except (TypeError, ValueError):  # pragma: no cover - defensive
                        timeout_s = EXEC_TOOL_TIMEOUT_S
                else:
                    timeout_s = EXEC_TOOL_TIMEOUT_S

        handle = await shell_manager.run(command, timeout=timeout_s)

        max_output_chars = _get_max_output_chars(self.prj, spec)
        output = _OutputAccumulator(max_output_chars)

        async def _read_stdout():
            async for chunk in handle.iter_stdout():
                output.add(chunk)

        async def _read_stderr():
            async for chunk in handle.iter_stderr():
                output.add(chunk)

        readers = [
            asyncio.create_task(_read_stdout()),
            asyncio.create_task(_read_stderr()),
        ]

        timed_out = False
        rc: Optional[int] = None
        try:
            rc = await handle.wait()
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await handle.terminate(grace_s=1.0)
            with contextlib.suppress(Exception):
                if handle.alive():
                    await handle.kill()
            with contextlib.suppress(Exception):
                await handle.wait()
            for reader in readers:
                reader.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
            raise
        except asyncio.TimeoutError:
            timed_out = True
            rc = None
        finally:
            pending_readers = [reader for reader in readers if not reader.done()]
            if pending_readers:
                await asyncio.gather(*pending_readers, return_exceptions=True)

        payload = {
            "output": output.render(),
            "exit_code": rc,
            "timed_out": timed_out,
        }
        return tools_base.ToolTextResponse(text=json.dumps(payload))

    async def openapi_spec(self, spec: ToolSpec) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Execute a shell command and return combined stdout/stderr, exit code, and timeout status. "
                f"Timeout is configurable via the timeout_s parameter or tool config and defaults to {EXEC_TOOL_TIMEOUT_S} seconds. "
                "Output is truncated to ~10KB."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Command to run (executed via system shell).",
                    },
                    "timeout_s": {
                        "type": "number",
                        "description": "Optional per-call timeout in seconds.",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        }


# registered via ToolFactory
