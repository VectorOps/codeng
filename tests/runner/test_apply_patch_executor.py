import asyncio
from pathlib import Path

import pytest

from knowlt.settings import ProjectSettings as KnowProjectSettings
from tests.stub_project import StubProject
from vocode import models, state
from vocode.runner.base import ExecutorFactory, ExecutorInput
from vocode.runner.executors.apply_patch_node import (
    ApplyPatchExecutor,
    ApplyPatchNode,
)
from vocode.settings import Settings


class PatchExecTestProject(StubProject):
    def __init__(self, base_path: Path) -> None:
        super().__init__(settings=Settings())
        self.base_path = base_path
        self.refresh_calls: list[list[object]] = []

    async def refresh(self, *, files) -> None:
        self.refresh_calls.append(list(files))


@pytest.mark.asyncio
async def test_apply_patch_executor_success(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("pre\n old\npost\n", encoding="utf-8")
    (tmp_path / "gone.txt").write_text("remove me", encoding="utf-8")

    patch_text = """*** Begin Patch
*** Update File: f.txt
 pre
- old
+ new
 post
*** Add File: new.txt
+ hello
*** Delete File: gone.txt
*** End Patch"""

    project = PatchExecTestProject(tmp_path)
    node = ApplyPatchNode(name="apply", format="v4a")
    run = state.WorkflowExecution(workflow_name="wf")
    patch_message = state.Message(role=models.Role.ASSISTANT, text=patch_text)
    run.add_message(patch_message)
    execution = run.create_node_execution(
        node=node.name,
        status=state.RunStatus.RUNNING,
        input_message_ids=[patch_message.id],
    )
    executor = ExecutorFactory.create_for_node(node, project=project)
    assert isinstance(executor, ApplyPatchExecutor)
    inp = ExecutorInput(execution=execution, run=run)

    steps = [step async for step in executor.run(inp)]
    await asyncio.sleep(0)

    assert len(steps) == 1
    step = steps[0]
    assert step.is_complete
    assert step.is_final
    assert step.outcome_name == "success"
    assert step.type is state.StepType.OUTPUT_MESSAGE
    assert step.message is not None
    assert "Applied patch successfully" in step.message.text

    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "pre\n new\npost\n"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == " hello"
    assert not (tmp_path / "gone.txt").exists()

    assert len(project.refresh_calls) == 1
    assert len(project.refresh_calls[0]) == 3


@pytest.mark.asyncio
async def test_apply_patch_executor_unsupported_format(tmp_path: Path) -> None:
    patch_text = """*** Begin Patch
*** End Patch"""

    project = PatchExecTestProject(tmp_path)
    node = ApplyPatchNode(name="apply", format="unknown")
    run = state.WorkflowExecution(workflow_name="wf")
    patch_message = state.Message(role=models.Role.ASSISTANT, text=patch_text)
    run.add_message(patch_message)
    execution = run.create_node_execution(
        node=node.name,
        status=state.RunStatus.RUNNING,
        input_message_ids=[patch_message.id],
    )
    executor = ExecutorFactory.create_for_node(node, project=project)
    assert isinstance(executor, ApplyPatchExecutor)
    inp = ExecutorInput(execution=execution, run=run)

    steps = [step async for step in executor.run(inp)]

    assert len(steps) == 1
    step = steps[0]
    assert step.is_complete
    assert step.is_final
    assert step.outcome_name == "fail"
    assert step.message is not None
    assert "Unsupported patch format" in step.message.text
    assert project.refresh_calls == []


@pytest.mark.asyncio
async def test_apply_patch_executor_rejects_knowlt_project_path(tmp_path: Path) -> None:
    patch_text = """*** Begin Patch
*** Add File: repo/f.txt
+ blocked
*** End Patch"""

    project = PatchExecTestProject(tmp_path)
    project.settings = Settings(
        know=KnowProjectSettings(
            project_name="proj",
            repo_name="repo",
            paths={"enable_project_paths": True},
        )
    )
    node = ApplyPatchNode(name="apply", format="v4a")
    run = state.WorkflowExecution(workflow_name="wf")
    patch_message = state.Message(role=models.Role.ASSISTANT, text=patch_text)
    run.add_message(patch_message)
    execution = run.create_node_execution(
        node=node.name,
        status=state.RunStatus.RUNNING,
        input_message_ids=[patch_message.id],
    )
    executor = ExecutorFactory.create_for_node(node, project=project)
    assert isinstance(executor, ApplyPatchExecutor)
    inp = ExecutorInput(execution=execution, run=run)

    steps = [step async for step in executor.run(inp)]

    assert len(steps) == 1
    step = steps[0]
    assert step.is_complete
    assert step.is_final
    assert step.outcome_name == "fail"
    assert step.message is not None
    assert "KnowLT virtual paths are read-only" in step.message.text
    assert not (tmp_path / "repo").exists()
    assert project.refresh_calls == []
