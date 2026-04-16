from typing import Any, List, Optional

import asyncio
import connect
import pytest

from vocode import state, models, settings as vocode_settings
from vocode.history.manager import HistoryManager
from vocode.runner.executors.llm.llm import LLMExecutor
from vocode.runner.executors.llm.models import LLMNode
from vocode.runner.executors.llm import helpers as llm_helpers
from vocode.runner.base import ExecutorInput
from tests.stub_project import StubProject


RECORDED_TOOL_CALLS: List[str] = []  # type: ignore


class FakeStreamHandle:
    def __init__(
        self,
        events: List[connect.StreamEvent],
        final_response: Optional[connect.AssistantMessage] = None,
    ) -> None:
        self._events = events
        self._final_response = final_response
        self._index = 0

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self._events):
            raise StopAsyncIteration
        event = self._events[self._index]
        self._index += 1
        return event

    async def final_response(self) -> connect.AssistantMessage:
        if self._final_response is not None:
            return self._final_response
        for event in reversed(self._events):
            if event.type == "response_end":
                return event.response
        raise RuntimeError("missing final response")


class FakeAsyncLLMClient:
    def __init__(self, stream_handle: FakeStreamHandle, **kwargs: Any) -> None:
        self._stream_handle = stream_handle
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, model: str, request: Any, options: Any = None):
        return self._stream_handle


class FailingAsyncLLMClient:
    def __init__(self, error: connect.ConnectError, **kwargs: Any) -> None:
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, model: str, request: Any, options: Any = None):
        raise self._error


def _assistant_response(
    text: str,
    *,
    tool_calls: Optional[List[connect.ToolCallBlock]] = None,
) -> connect.AssistantMessage:
    content: List[connect.AssistantContentBlock] = [connect.TextBlock(text=text)]
    for tool_call in tool_calls or []:
        content.append(tool_call)
    return connect.AssistantMessage(
        provider="openai",
        model="gpt-3.5-turbo",
        api_family="openai-responses",
        content=content,
        finish_reason="stop",
        usage=connect.Usage(
            input_tokens=5,
            output_tokens=len(text),
            total_tokens=5 + len(text),
            completeness="final",
        ),
    )


def _stream_with_text(text: str) -> FakeStreamHandle:
    response = _assistant_response(text)
    return FakeStreamHandle(
        [
            connect.TextDeltaEvent(index=0, delta=text),
            connect.ResponseEndEvent(response=response),
        ],
        final_response=response,
    )


def _timeout_stream() -> FakeStreamHandle:
    class _BlockingStreamHandle(FakeStreamHandle):
        async def __anext__(self):
            await asyncio.sleep(3600)
            raise StopAsyncIteration

    return _BlockingStreamHandle([])


def _empty_then_timeout_stream() -> FakeStreamHandle:
    class _EmptyTimeoutStreamHandle(FakeStreamHandle):
        def __init__(self, events: Optional[List[connect.StreamEvent]] = None) -> None:
            self._index = 0

        def __aiter__(self):
            self._index = 0
            return self

        async def __anext__(self):
            if self._index == 0:
                self._index += 1
                return connect.TextEndEvent(index=0, text="")
            await asyncio.sleep(3600)
            raise StopAsyncIteration

    return _EmptyTimeoutStreamHandle()


def _partial_then_timeout_stream(text: str) -> FakeStreamHandle:
    class _PartialTimeoutStreamHandle(FakeStreamHandle):
        def __init__(self, partial_text: str) -> None:
            self._partial_text = partial_text
            self._index = 0

        def __aiter__(self):
            self._index = 0
            return self

        async def __anext__(self):
            if self._index == 0:
                self._index += 1
                return connect.TextDeltaEvent(index=0, delta=self._partial_text)
            await asyncio.sleep(3600)
            raise StopAsyncIteration

    return _PartialTimeoutStreamHandle(text)


def _tool_call_response(tool_call_id: str) -> connect.AssistantMessage:
    tool_call = connect.ToolCallBlock(
        id=tool_call_id,
        name="echo",
        arguments={"x": 1},
    )
    return _assistant_response("Tool answer", tool_calls=[tool_call])


def _build_execution_with_input(
    node_name: str, text: str
) -> tuple[state.WorkflowExecution, state.NodeExecution]:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    user_msg = state.Message(role=models.Role.USER, text=text)
    history.upsert_message(run, user_msg)
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node=node_name,
            input_message_ids=[user_msg.id],
            status=state.RunStatus.RUNNING,
        ),
    )
    return run, execution


@pytest.mark.asyncio
async def test_llm_executor_with_connect_mock_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: FakeAsyncLLMClient(
            _stream_with_text("It's simple to use and easy to get started"),
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-1",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-1", "Hey, I'm a mock request")
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
async def test_llm_executor_counts_cached_tokens_toward_round_prompt_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = connect.AssistantMessage(
        provider="openai",
        model="gpt-3.5-turbo",
        api_family="openai-responses",
        content=[connect.TextBlock(text="cached usage response")],
        finish_reason="stop",
        usage=connect.Usage(
            input_tokens=5,
            output_tokens=3,
            cache_read_tokens=7,
            total_tokens=15,
            completeness="final",
        ),
    )
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: FakeAsyncLLMClient(
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="cached usage response"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-cached-usage",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-cached-usage", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    output_steps = [s for s in steps if s.type == state.StepType.OUTPUT_MESSAGE]
    assert output_steps
    final_message_step = output_steps[-1]
    assert final_message_step.llm_usage is not None
    assert final_message_step.llm_usage.prompt_tokens == 12
    assert final_message_step.llm_usage.completion_tokens == 3


@pytest.mark.asyncio
async def test_llm_executor_emits_preview_usage_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}

    def fake_client_factory(*args, **kwargs) -> FakeAsyncLLMClient:
        call_count["n"] += 1
        return FakeAsyncLLMClient(_stream_with_text("preview response"), **kwargs)

    monkeypatch.setattr(connect, "AsyncLLMClient", fake_client_factory)

    project = StubProject()
    node = LLMNode(
        name="node-preview-usage",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-preview-usage", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    agen = executor.run(inp)
    first_step = await agen.__anext__()

    assert call_count["n"] == 0
    assert first_step.type == state.StepType.OUTPUT_MESSAGE
    assert first_step.is_complete is False
    assert first_step.message is None
    assert first_step.llm_usage is not None
    assert first_step.llm_usage.prompt_tokens == 0
    assert first_step.llm_usage.completion_tokens == 0
    assert first_step.llm_usage.cost_dollars == 0.0
    assert first_step.llm_usage.model_name == "gpt-3.5-turbo"

    async for _ in agen:
        pass

    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_llm_executor_preview_usage_reuses_last_step_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}

    def fake_client_factory(*args, **kwargs) -> FakeAsyncLLMClient:
        call_count["n"] += 1
        return FakeAsyncLLMClient(_stream_with_text("preview response"), **kwargs)

    monkeypatch.setattr(connect, "AsyncLLMClient", fake_client_factory)

    project = StubProject()
    node = LLMNode(
        name="node-preview-prior-usage",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-preview-prior-usage", "Hi")
    run.last_step_llm_usage = state.LLMUsageStats(
        prompt_tokens=21,
        completion_tokens=8,
        cost_dollars=0.75,
        model_name="older-model",
    )
    inp = ExecutorInput(execution=execution, run=run)

    agen = executor.run(inp)
    first_step = await agen.__anext__()

    assert call_count["n"] == 0
    assert first_step.llm_usage is not None
    assert first_step.llm_usage.prompt_tokens == 21
    assert first_step.llm_usage.completion_tokens == 8
    assert first_step.llm_usage.cost_dollars == 0.75
    assert first_step.llm_usage.model_name == "gpt-3.5-turbo"

    async for _ in agen:
        pass

    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_llm_executor_uses_only_current_node_execution_messages_when_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_requests: List[Any] = []
    response = _assistant_response("fresh answer")

    class CapturingAsyncLLMClient(FakeAsyncLLMClient):
        def stream(self, model: str, request: Any, options: Any = None):
            captured_requests.append(request)
            return super().stream(model, request, options)

    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: CapturingAsyncLLMClient(
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="fresh answer"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="decompile",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")

    first_input = state.Message(role=models.Role.USER, text="first pass input")
    history.upsert_message(run, first_input)
    first_execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="decompile",
            input_message_ids=[first_input.id],
            status=state.RunStatus.FINISHED,
        ),
    )
    first_output = state.Message(role=models.Role.ASSISTANT, text="old result")
    history.upsert_message(run, first_output)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=first_execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=first_output.id,
            is_complete=True,
            is_final=True,
        ),
    )

    second_input = state.Message(role=models.Role.USER, text="second pass input")
    history.upsert_message(run, second_input)
    reset_execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="decompile",
            input_message_ids=[second_input.id],
            status=state.RunStatus.RUNNING,
        ),
    )

    inp = ExecutorInput(execution=reset_execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert steps
    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert [message.content for message in request.messages] == ["second pass input"]


@pytest.mark.asyncio
async def test_llm_executor_timeouts_retry_and_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}

    def fake_client_factory(*args, **kwargs) -> FakeAsyncLLMClient:
        call_count["n"] += 1
        return FakeAsyncLLMClient(_timeout_stream(), **kwargs)

    monkeypatch.setattr(connect, "AsyncLLMClient", fake_client_factory)

    project = StubProject()
    node = LLMNode(
        name="node-timeouts",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        start_timeout=0.01,
        response_timeout=0.01,
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-timeouts", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert call_count["n"] == 4
    assert steps
    final_step = steps[-1]
    assert final_step.type == state.StepType.REJECTION
    assert final_step.is_complete is True


@pytest.mark.asyncio
async def test_llm_executor_retry_clears_partial_streamed_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}

    def fake_client_factory(*args, **kwargs) -> FakeAsyncLLMClient:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return FakeAsyncLLMClient(
                _partial_then_timeout_stream("Applying stale partial. "),
                **kwargs,
            )
        return FakeAsyncLLMClient(_stream_with_text("Final retry answer"), **kwargs)

    monkeypatch.setattr(connect, "AsyncLLMClient", fake_client_factory)

    project = StubProject()
    node = LLMNode(
        name="node-retry-partial-reset",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        response_timeout=0.01,
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-retry-partial-reset", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert call_count["n"] == 2
    output_steps = [s for s in steps if s.type == state.StepType.OUTPUT_MESSAGE]
    assert output_steps
    final_message_step = output_steps[-1]
    assert final_message_step.message is not None
    assert final_message_step.message.text == "Final retry answer"
    assert "Applying stale partial." not in final_message_step.message.text
    assert final_message_step.is_complete is True


@pytest.mark.asyncio
async def test_llm_executor_rejects_when_max_rounds_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _tool_call_response("call-prior")
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: FakeAsyncLLMClient(
            FakeStreamHandle(
                [connect.ResponseEndEvent(response=response)],
                final_response=response,
            ),
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-max-rounds",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        max_rounds=1,
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-max-rounds", "Hi")
    prior_message = state.Message(
        role=models.Role.ASSISTANT,
        text="Tool answer",
        tool_call_requests=[
            state.ToolCallReq(
                id="call-prior",
                name="echo",
                arguments={"x": 1},
            )
        ],
    )
    project.history.upsert_message(run, prior_message)
    project.history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=prior_message.id,
            is_complete=True,
        ),
    )
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert len(steps) == 1
    assert steps[0].type == state.StepType.REJECTION
    assert steps[0].message is not None
    assert "exceeded max_rounds=1" in steps[0].message.text


@pytest.mark.asyncio
async def test_llm_executor_start_timeout_ignores_empty_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}

    def fake_client_factory(*args, **kwargs) -> FakeAsyncLLMClient:
        call_count["n"] += 1
        return FakeAsyncLLMClient(_empty_then_timeout_stream(), **kwargs)

    monkeypatch.setattr(connect, "AsyncLLMClient", fake_client_factory)

    project = StubProject()
    node = LLMNode(
        name="node-start-timeout-empty",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        start_timeout=0.01,
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-start-timeout-empty", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert call_count["n"] == 4
    assert steps
    assert all(
        s.type != state.StepType.OUTPUT_MESSAGE or not s.message or not s.message.text
        for s in steps
    )
    final_step = steps[-1]
    assert final_step.type == state.StepType.REJECTION
    assert final_step.is_complete is True


@pytest.mark.asyncio
async def test_llm_executor_populates_tool_spec_on_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call = connect.ToolCallBlock(
        id="call_1",
        name="echo",
        arguments={},
    )
    response = _assistant_response("Tool answer", tool_calls=[tool_call])
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: FakeAsyncLLMClient(
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="Tool answer"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            **kwargs,
        ),
    )

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

    run, execution = _build_execution_with_input("node-tools-call", "Hi")
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
    tools = await executor._build_connect_tools(effective_specs)
    assert tools is not None
    assert len(tools) == 1

    tool = tools[0]
    assert tool.name == "echo"
    params = tool.input_schema["properties"]
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
    tools = await executor._build_connect_tools(effective_specs)
    assert tools is None


@pytest.mark.asyncio
async def test_llm_executor_outcome_tag_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: FakeAsyncLLMClient(
            _stream_with_text("Tagged answer\nOUTCOME: success"),
            **kwargs,
        ),
    )

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

    run, execution = _build_execution_with_input("node-outcome-tag", "Hi")
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
    tool_call = connect.ToolCallBlock(
        id="call_1",
        name=llm_helpers.CHOOSE_OUTCOME_TOOL_NAME,
        arguments={"outcome": "success"},
    )
    RECORDED_TOOL_CALLS.append(tool_call.name)
    response = _assistant_response("Functional answer", tool_calls=[tool_call])
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: FakeAsyncLLMClient(
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="Functional answer"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            **kwargs,
        ),
    )

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

    run, execution = _build_execution_with_input("node-outcome-function", "Hi")
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


@pytest.mark.asyncio
async def test_llm_executor_rejects_chatgpt_without_authorization() -> None:
    project = StubProject()
    project.credentials.has_active_authorization = lambda provider: asyncio.sleep(
        0, result=False
    )
    node = LLMNode(
        name="node-chatgpt-auth",
        type="llm",
        model="chatgpt/gpt-4o",
        system="You are a test assistant.",
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-chatgpt-auth", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert len(steps) == 1
    assert steps[0].type == state.StepType.REJECTION
    assert steps[0].message is not None
    assert "Run /auth login chatgpt" in steps[0].message.text


@pytest.mark.asyncio
async def test_llm_executor_surfaces_structured_connect_error_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = connect.PermanentProviderError(
        connect.ErrorInfo(
            code="provider_error",
            message="Provider request failed",
            provider="chatgpt",
            api_family="chatgpt-responses",
            status_code=400,
            retryable=False,
            raw={"error": {"message": "bad request", "type": "invalid_request_error"}},
        )
    )
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: FailingAsyncLLMClient(error, **kwargs),
    )

    project = StubProject()
    node = LLMNode(
        name="node-connect-error",
        type="llm",
        model="chatgpt/gpt-5.4-mini",
        system="You are a test assistant.",
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-connect-error", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert len(steps) == 1
    rejection = steps[0]
    assert rejection.type == state.StepType.REJECTION
    assert rejection.message is not None
    assert "provider=chatgpt" in rejection.message.text
    assert "api_family=chatgpt-responses" in rejection.message.text
    assert "status_code=400" in rejection.message.text
    assert "code=provider_error" in rejection.message.text
    assert "retryable=False" in rejection.message.text
    assert '"type": "invalid_request_error"' in rejection.message.text
