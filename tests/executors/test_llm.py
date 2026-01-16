from typing import Any, List, Optional

import litellm
import pytest

from vocode import state, models, settings as vocode_settings
from vocode.runner.executors.llm.llm import LLMExecutor
from vocode.runner.executors.llm.models import LLMNode
from vocode.runner.executors.llm import helpers as llm_helpers
from vocode.runner.base import ExecutorInput
from tests.stub_project import StubProject


RECORDED_TOOL_CALLS: List[str] = []  # type: ignore


class FakeDelta:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoiceChunk:
    def __init__(self, content: str) -> None:
        self.delta = FakeDelta(content)


class FakeChunk:
    def __init__(self, content: str) -> None:
        self.choices = [FakeChoiceChunk(content)]


class FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, name: str, arguments: str) -> None:
        self.id = "call_1"
        self.type = "function"
        self.function = FakeFunction(name, arguments)


class FakeMessage:
    def __init__(self, content: str, tool_calls: Optional[List[Any]] = None) -> None:
        self.content = content
        self.tool_calls = list(tool_calls or [])


class FakeUsage:
    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeChoiceResponse:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message


class FakeResponse:
    def __init__(
        self,
        content: str,
        *,
        tool_calls: Optional[List[Any]] = None,
    ) -> None:
        self.choices = [FakeChoiceResponse(FakeMessage(content, tool_calls))]
        self.usage = FakeUsage(prompt_tokens=5, completion_tokens=len(content))


@pytest.mark.asyncio
async def test_llm_executor_with_litellm_mock_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_acompletion(*args: Any, **kwargs: Any) -> Any:
        async def gen() -> Any:
            yield FakeChunk("It's simple to use and easy to get started")

        return gen()

    def fake_stream_chunk_builder(chunks: List[Any], messages: Any) -> Any:
        parts: List[str] = []
        for chunk in chunks:
            choice0 = chunk.choices[0]
            if choice0.delta.content:
                parts.append(choice0.delta.content)
        full_text = "".join(parts)

        return FakeResponse(full_text)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(
        litellm,
        "stream_chunk_builder",
        fake_stream_chunk_builder,
    )

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

    assert steps
    for step in steps:
        assert step.execution is execution

    output_steps = [s for s in steps if s.type == state.StepType.OUTPUT_MESSAGE]
    assert output_steps
    final_message_step = output_steps[-1]

    assert final_message_step.message is not None
    assert final_message_step.message.role == models.Role.ASSISTANT
    assert (
        final_message_step.message.text == "It's simple to use and easy to get started"
    )

    assert final_message_step.llm_usage is not None
    assert isinstance(final_message_step.llm_usage.prompt_tokens, int)
    assert isinstance(final_message_step.llm_usage.completion_tokens, int)

    for s in output_steps[:-1]:
        assert s.is_complete is False
    assert final_message_step.is_complete is True


@pytest.mark.asyncio
async def test_llm_executor_populates_tool_spec_on_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_acompletion(*args: Any, **kwargs: Any) -> Any:
        async def gen() -> Any:
            yield FakeChunk("Tool answer")

        return gen()

    def fake_stream_chunk_builder(chunks: List[Any], messages: Any) -> Any:
        parts: List[str] = []
        for chunk in chunks:
            choice0 = chunk.choices[0]
            if choice0.delta.content:
                parts.append(choice0.delta.content)
        full_text = "".join(parts)

        tool_args = "{}"
        tool_call = FakeToolCall("echo", tool_args)
        return FakeResponse(full_text, tool_calls=[tool_call])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(litellm, "stream_chunk_builder", fake_stream_chunk_builder)

    project = StubProject()

    global_tool = vocode_settings.ToolSpec(
        name="echo",
        enabled=True,
        config={"x": 2},
    )
    project.settings.tools = [global_tool]

    node_tool = vocode_settings.ToolSpec(
        name="echo",
        enabled=True,
        config={"x": 1},
    )

    node = LLMNode(
        name="node-tools-call",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        tools=[node_tool],
    )
    executor = LLMExecutor(config=node, project=project)

    user_msg = state.Message(role=models.Role.USER, text="Hi")
    execution = state.NodeExecution(
        node="node-tools-call",
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

    output_steps = [s for s in steps if s.type == state.StepType.OUTPUT_MESSAGE]
    assert output_steps
    final_message_step = output_steps[-1]
    assert final_message_step.message is not None
    tool_reqs = final_message_step.message.tool_call_requests
    assert len(tool_reqs) == 1

    req = tool_reqs[0]
    assert req.name == "echo"
    assert req.tool_spec is not None
    assert isinstance(req.tool_spec, vocode_settings.ToolSpec)
    assert req.tool_spec.config.get("x") == 2


@pytest.mark.asyncio
async def test_llm_executor_build_tools_uses_effective_specs_config_merge() -> None:
    project = StubProject()

    global_tool = vocode_settings.ToolSpec(
        name="echo",
        enabled=True,
        config={"x": 2, "global": True},
    )
    project.settings.tools = [global_tool]

    class EchoTool:
        async def openapi_spec(
            self,
            spec: vocode_settings.ToolSpec,
        ) -> dict[str, Any]:
            return {
                "name": spec.name,
                "parameters": {
                    "type": "object",
                    "properties": dict(spec.config),
                },
            }

        async def run(
            self,
            spec: vocode_settings.ToolSpec,
            args: Any,
        ) -> None:
            return None

    project.tools["echo"] = EchoTool()

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
    effective_specs = llm_helpers.build_effective_tool_specs(
        project,
        node,
    )
    tools = await executor._build_tools(effective_specs)
    assert tools is not None
    assert len(tools) == 1

    tool = tools[0]
    assert tool["type"] == "function"
    fn = tool["function"]
    params = fn["parameters"]["properties"]
    assert params["x"] == 2
    assert params["local"] is True
    assert params["global"] is True


@pytest.mark.asyncio
async def test_llm_executor_build_tools_respects_global_enabled_override() -> None:
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

    class EchoTool:
        async def openapi_spec(
            self,
            spec: vocode_settings.ToolSpec,
        ) -> dict[str, Any]:
            return {
                "name": spec.name,
                "parameters": {
                    "type": "object",
                    "properties": dict(spec.config),
                },
            }

        async def run(
            self,
            spec: vocode_settings.ToolSpec,
            args: Any,
        ) -> None:
            return None

    project.tools["echo"] = EchoTool()
    effective_specs = llm_helpers.build_effective_tool_specs(
        project,
        node,
    )
    tools = await executor._build_tools(effective_specs)
    assert tools is None


@pytest.mark.asyncio
async def test_llm_executor_outcome_tag_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_acompletion(*args: Any, **kwargs: Any) -> Any:
        async def gen() -> Any:
            yield FakeChunk("Tagged answer\nOUTCOME: success")

        return gen()

    def fake_stream_chunk_builder(chunks: List[Any], messages: Any) -> Any:
        parts: List[str] = []
        for chunk in chunks:
            choice0 = chunk.choices[0]
            if choice0.delta.content:
                parts.append(choice0.delta.content)
        full_text = "".join(parts)
        return FakeResponse(full_text)

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(litellm, "stream_chunk_builder", fake_stream_chunk_builder)

    project = StubProject()
    node = LLMNode(
        name="node-outcome-tag",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        outcomes=[
            models.OutcomeSlot(name="success"),
            models.OutcomeSlot(name="failure"),
        ],
        outcome_strategy=models.OutcomeStrategy.TAG,
    )
    executor = LLMExecutor(config=node, project=project)

    user_msg = state.Message(role=models.Role.USER, text="Hi")
    execution = state.NodeExecution(
        node="node-outcome-tag",
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

    output_steps = [s for s in steps if s.type == state.StepType.OUTPUT_MESSAGE]
    assert output_steps
    final_message_step = output_steps[-1]

    assert final_message_step.message is not None
    assert "OUTCOME:" not in final_message_step.message.text

    assert final_message_step.outcome_name == "success"

    for s in output_steps[:-1]:
        assert s.is_complete is False
    assert final_message_step.is_complete is True


@pytest.mark.asyncio
async def test_llm_executor_outcome_function_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RECORDED_TOOL_CALLS.clear()

    async def fake_acompletion(*args: Any, **kwargs: Any) -> Any:
        async def gen() -> Any:
            yield FakeChunk("Functional answer")

        return gen()

    def fake_stream_chunk_builder(chunks: List[Any], messages: Any) -> Any:
        parts: List[str] = []
        for chunk in chunks:
            choice0 = chunk.choices[0]
            if choice0.delta.content:
                parts.append(choice0.delta.content)
        full_text = "".join(parts)

        outcome_args = '{"outcome": "success"}'
        tool_call = FakeToolCall(llm_helpers.CHOOSE_OUTCOME_TOOL_NAME, outcome_args)
        RECORDED_TOOL_CALLS.append(tool_call.function.name)
        return FakeResponse(full_text, tool_calls=[tool_call])

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(litellm, "stream_chunk_builder", fake_stream_chunk_builder)

    project = StubProject()
    node = LLMNode(
        name="node-outcome-function",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        outcomes=[
            models.OutcomeSlot(name="success"),
            models.OutcomeSlot(name="failure"),
        ],
        outcome_strategy=models.OutcomeStrategy.FUNCTION,
    )
    executor = LLMExecutor(config=node, project=project)

    user_msg = state.Message(role=models.Role.USER, text="Hi")
    execution = state.NodeExecution(
        node="node-outcome-function",
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

    assert RECORDED_TOOL_CALLS == [llm_helpers.CHOOSE_OUTCOME_TOOL_NAME]

    output_steps = [s for s in steps if s.type == state.StepType.OUTPUT_MESSAGE]
    assert output_steps
    final_message_step = output_steps[-1]

    assert final_message_step.message is not None
    assert final_message_step.message.text == "Functional answer"
    assert all(
        all(
            req.name != llm_helpers.CHOOSE_OUTCOME_TOOL_NAME
            for req in (step.message.tool_call_requests or [])
        )
        for step in steps
        if step.message is not None
    )

    assert final_message_step.outcome_name == "success"

    for s in output_steps[:-1]:
        assert s.is_complete is False
    assert final_message_step.is_complete is True
