import asyncio
import os
from pathlib import Path

import pytest

from vocode import state, models
from vocode.logger import logger
from vocode.proc.manager import ProcessManager
from vocode.runner.base import ExecutorInput
from vocode.runner.executors.exec_node import ExecNode, ExecExecutor
from tests.stub_project import StubProject


pytestmark = [
    pytest.mark.skipif(os.name != "posix", reason="POSIX-only tests"),
]


def test_exec_executor_streaming(tmp_path: Path) -> None:
    async def scenario() -> None:
        pm = ProcessManager(backend_name="local", default_cwd=tmp_path)
        proj = StubProject(process_manager=pm)

        node = ExecNode(
            name="exec1",
            type="exec",
            command="printf 'a'; sleep 0.1; printf 'b'",
            outcomes=[models.OutcomeSlot(name="done")],
        )

        execution = state.NodeExecution(
            node=node.name,
            status=state.RunStatus.RUNNING,
        )
        run = state.WorkflowExecution(
            workflow_name="test",
        )

        executor = ExecExecutor(config=node, project=proj)  # type: ignore[arg-type]
        inp = ExecutorInput(execution=execution, run=run)

        steps: list[state.Step] = []
        async for step in executor.run(inp):
            steps.append(step)
            print(step.message)

        assert steps

        header = f"> {node.command}"
        first_text = steps[0].message.text if steps[0].message else ""
        assert header in first_text

        final = steps[-1]
        final_text = final.message.text if final.message else ""
        assert "a" in final_text
        assert "b" in final_text

        assert final.is_complete is True
        assert final.is_final is True
        assert final.outcome_name == "done"

        await pm.shutdown()

    asyncio.run(scenario())
