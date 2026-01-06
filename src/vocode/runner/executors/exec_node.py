from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional
from typing import List, Set
from typing import TYPE_CHECKING

from pydantic import model_validator

from vocode import models, state, settings
from vocode.runner.base import BaseExecutor, ExecutorFactory, ExecutorInput

if TYPE_CHECKING:
    from vocode.project import Project


class ExecNode(models.Node):
    type: str = "exec"

    command: str
    timeout_s: Optional[float] = None
    expected_return_code: Optional[int] = None
    message: Optional[str] = None

    @model_validator(mode="after")
    def _validate_outcomes_vs_expected_code(self) -> "ExecNode":
        exp = self.expected_return_code
        if exp is None:
            if len(self.outcomes) > 1:
                raise ValueError(
                    "ExecNode: when 'expected_return_code' is not provided, at most one outcome is allowed"
                )
        else:
            names: Set[str] = {o.name for o in self.outcomes}
            if names != {"success", "fail"}:
                raise ValueError(
                    "ExecNode: when 'expected_return_code' is provided, outcomes must be exactly {'success', 'fail'}"
                )
        return self


def _compute_max_output_chars(project: "Project") -> int:
    max_chars = settings.EXEC_TOOL_MAX_OUTPUT_CHARS_DEFAULT
    proj_settings = project.settings
    if proj_settings is not None:
        tool_settings = proj_settings.tool_settings
        if tool_settings is not None and tool_settings.exec_tool is not None:
            exec_settings = tool_settings.exec_tool
            if exec_settings.max_output_chars and exec_settings.max_output_chars > 0:
                max_chars = int(exec_settings.max_output_chars)
    return max_chars


@ExecutorFactory.register("exec")
class ExecExecutor(BaseExecutor):
    def __init__(self, config: ExecNode, project: "Project"):
        super().__init__(config=config, project=project)
        self.config = config

    async def run(self, inp: ExecutorInput) -> AsyncIterator[state.Step]:
        cfg = self.config
        shell_manager = self.project.shells
        if shell_manager is None:
            raise RuntimeError("ExecExecutor requires project.shells (ShellManager)")

        handle = await shell_manager.run(cfg.command, timeout=cfg.timeout_s)

        header_parts: List[str] = []
        if cfg.message:
            header_parts.append(cfg.message)
        header_parts.append(f"> {cfg.command}")
        header = "\n".join(header_parts)

        output = header + "\n"
        queue: asyncio.Queue[str] = asyncio.Queue()

        async def _pump_stdout() -> None:
            async for chunk in handle.iter_stdout():
                await queue.put(chunk)

        async def _pump_stderr() -> None:
            async for chunk in handle.iter_stderr():
                await queue.put(chunk)

        pump_out = asyncio.create_task(_pump_stdout())
        pump_err = asyncio.create_task(_pump_stderr())
        wait_task = asyncio.create_task(handle.wait())

        message = state.Message(
            role=models.Role.ASSISTANT,
            text=output,
        )
        step = state.Step(
            execution=inp.execution,
            type=state.StepType.OUTPUT_MESSAGE,
            message=message,
            is_complete=False,
            is_final=False,
        )

        timed_out = False
        rc: Optional[int] = None
        max_output_chars = _compute_max_output_chars(self.project)

        yield step

        while True:
            try:
                chunk = await asyncio.wait_for(queue.get(), timeout=1)
                if len(output) < max_output_chars:
                    remaining = max_output_chars - len(output)
                    if remaining > 0:
                        output += chunk[:remaining]
                if step.message is not None:
                    step.message.text = output
                yield step
            except asyncio.TimeoutError:
                pass

            if wait_task.done() and queue.empty():
                break

        pump_out.cancel()
        pump_err.cancel()
        await asyncio.gather(pump_out, pump_err, return_exceptions=True)

        try:
            rc = wait_task.result()
        except asyncio.TimeoutError:
            timed_out = True
            rc = None
        except Exception:
            try:
                rc = await handle.wait()
            except Exception:
                rc = None

        outcome_name: Optional[str] = None
        if cfg.expected_return_code is not None:
            if (not timed_out) and (rc == cfg.expected_return_code):
                outcome_name = "success"
            else:
                outcome_name = "fail"
        else:
            if len(cfg.outcomes) == 1:
                outcome_name = cfg.outcomes[0].name

        if step.message is not None:
            step.message.text = output.rstrip("\n")
        step.is_complete = True
        step.is_final = True
        step.outcome_name = outcome_name
        yield step
