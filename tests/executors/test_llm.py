from typing import Any, List

import litellm
import pytest

from vocode import state, models, settings as vocode_settings
from vocode.runner.executors.llm.llm import LLMExecutor
from vocode.runner.executors.llm.models import LLMNode
from vocode.runner.base import ExecutorInput
from tests.stub_project import StubProject


@pytest.mark.asyncio
async def test_llm_executor_with_litellm_mock_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_acompletion = litellm.acompletion

    async def fake_acompletion(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault(
            "mock_response",
            "It's simple to use and easy to get started",
        )
        kwargs.setdefault("stream", True)
        return await original_acompletion(*args, **kwargs)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)

    project = StubProject()
    node = LLMNode(
        name="node-1",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
    )
    executor = LLMExecutor(config=node, project=project)

    user_msg = state.Message(role=models.Role.USER, text="Hey, I'm a mock request")
    execution = state.NodeExecution(
        node="node-1",
        input_messages=[user_msg],
        status=state.RunStatus.RUNNING,
    )
    run = state.WorkflowExecution(
        workflow_name="wf",
        node_executions={execution.id: execution},
        steps=[],
    )
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert len(steps) >= 2
    for step in steps:
        assert step.execution is execution

    completion_step = steps[-1]
    final_message_step = steps[-2]

    assert completion_step.type == state.StepType.COMPLETION
    assert completion_step.message is None

    assert final_message_step.type == state.StepType.OUTPUT_MESSAGE
    assert final_message_step.message is not None
    assert final_message_step.message.role == models.Role.ASSISTANT
    assert (
        final_message_step.message.text
        == "It's simple to use and easy to get started"
    )

    assert final_message_step.llm_usage is not None
    assert isinstance(final_message_step.llm_usage.prompt_tokens, int)
    assert isinstance(final_message_step.llm_usage.completion_tokens, int)

    assert completion_step.llm_usage is final_message_step.llm_usage


def test_llm_executor_build_tools_uses_effective_specs_config_merge() -> None:
    project = StubProject()

    global_tool = vocode_settings.ToolSpec(
        name="echo",
        enabled=True,
        config={"x": 2, "global": True},
    )
    project.settings.tools = [global_tool]

    node_tool = vocode_settings.ToolSpec(
        name="echo",
        enabled=True,
        config={"x": 1, "local": True},
    )

    node = LLMNode(
        name="node-tools",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        tools=[node_tool],
    )

    executor = LLMExecutor(config=node, project=project)

    tools = executor._build_tools()
    assert tools is not None
    assert len(tools) == 1

    tool = tools[0]
    assert tool["x"] == 2
    assert tool["local"] is True
    assert tool["global"] is True


def test_llm_executor_build_tools_respects_global_enabled_override() -> None:
    project = StubProject()

    global_tool = vocode_settings.ToolSpec(
        name="echo",
        enabled=False,
        config={"x": 2},
    )
    project.settings.tools = [global_tool]

    node_tool = vocode_settings.ToolSpec(
        name="echo",
        enabled=True,
        config={"x": 1},
    )

    node = LLMNode(
        name="node-tools",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        tools=[node_tool],
    )

    executor = LLMExecutor(config=node, project=project)

    tools = executor._build_tools()
    assert tools is None