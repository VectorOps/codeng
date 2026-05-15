import asyncio
import connect
import logging
import pytest
from typing import Any, List, Optional

from vocode import state, models, settings as vocode_settings
from vocode.history.manager import HistoryManager
from vocode.mcp import models as mcp_models
from vocode.mcp import naming as mcp_naming
from vocode.mcp.service import MCPWorkflowSessionChange
from vocode.mcp import tool_materialization as mcp_tool_materialization
from vocode.manager.base import Workflow
from vocode.runner.runner import Runner
from vocode.runner.executors.llm.compaction import build_compaction_instructions
from vocode.runner.executors.llm.compaction import CompactionSettings
from vocode.runner.executors.llm.compaction import CompactionSummaryState
from vocode.runner.executors.llm.compaction import LLMExecutionCompactionState
from vocode.runner.executors.llm.compaction import LLMExecutionState
from vocode.runner.executors.llm.compaction import build_summary_generation_prompt
from vocode.runner.executors.llm.compaction import resolve_compaction_instructions
from vocode.runner.executors.llm.compaction import resolve_compaction_system_prompt
from vocode.runner.executors.llm.compaction import serialize_messages_to_transcript
from vocode.runner.executors.llm.compaction.service import select_compaction_cut_index
from vocode.runner.executors.llm.llm import LLMExecutor
from vocode.runner.executors.llm.compaction import should_trigger_compaction
from vocode.runner.executors.llm.models import LLMNodeMCPSettings
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

    async def generate(
        self,
        model: str,
        request: Any,
        provider: Optional[str] = None,
        options: Any = None,
    ) -> connect.AssistantMessage:
        _ = model, request, provider, options
        return await self._stream_handle.final_response()


class RecordingAsyncLLMClient(FakeAsyncLLMClient):
    def __init__(
        self, stream_handles: List[FakeStreamHandle], requests: List[Any], **kwargs: Any
    ) -> None:
        self._stream_handles = stream_handles
        self._requests = requests
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, model: str, request: Any, options: Any = None):
        self._requests.append(request)
        if not self._stream_handles:
            raise RuntimeError("missing stream handle")
        return self._stream_handles.pop(0)

    async def generate(
        self,
        model: str,
        request: Any,
        provider: Optional[str] = None,
        options: Any = None,
    ) -> connect.AssistantMessage:
        _ = model, provider, options
        self._requests.append(request)
        if not self._stream_handles:
            raise RuntimeError("missing stream handle")
        return await self._stream_handles[0].final_response()


class SummaryThenStreamAsyncLLMClient(FakeAsyncLLMClient):
    def __init__(
        self,
        summary_response: connect.AssistantMessage,
        stream_handle: FakeStreamHandle,
        summary_requests: List[Any],
        stream_requests: List[Any],
        **kwargs: Any,
    ) -> None:
        super().__init__(stream_handle, **kwargs)
        self._summary_response = summary_response
        self._summary_requests = summary_requests
        self._stream_requests = stream_requests

    async def generate(
        self,
        model: str,
        request: Any,
        provider: Optional[str] = None,
        options: Any = None,
    ) -> connect.AssistantMessage:
        _ = model, provider, options
        self._summary_requests.append(request)
        return self._summary_response

    def stream(self, model: str, request: Any, options: Any = None):
        self._stream_requests.append(request)
        return super().stream(model, request, options)


class FailingAsyncLLMClient:
    def __init__(self, error: connect.ConnectError, **kwargs: Any) -> None:
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, model: str, request: Any, options: Any = None):
        raise self._error


class SequenceAsyncLLMClient:
    def __init__(self, actions: List[Any], requests: List[Any], **kwargs: Any) -> None:
        self._actions = actions
        self._requests = requests

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, model: str, request: Any, options: Any = None):
        self._requests.append(request)
        if not self._actions:
            raise RuntimeError("missing action")
        action = self._actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    async def generate(
        self,
        model: str,
        request: Any,
        provider: Optional[str] = None,
        options: Any = None,
    ) -> connect.AssistantMessage:
        _ = model, provider, options
        self._requests.append(request)
        if not self._actions:
            raise RuntimeError("missing action")
        action = self._actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return await action.final_response()


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


class FakeMCPService:
    def __init__(self) -> None:
        self.applied_workflow_requirements: List[tuple[str, List[str]]] = []
        self.cleared_workflow_requirements: List[str] = []
        self.started_sources: List[str] = []
        self.closed_sources: List[str] = []
        self.refreshed_sources: List[str] = []
        self.sessions: dict[str, object] = {}
        self.cached_tools: dict[str, dict[str, Any]] = {}
        self.workflow_sources_by_owner: dict[tuple[str, str], set[str]] = {}
        self.workflow_owners_by_source: dict[str, set[tuple[str, str]]] = {}

    def get_session(self, source_name: str) -> Optional[object]:
        return self.sessions.get(source_name)

    async def start_session(self, source_name: str) -> object:
        self.started_sources.append(source_name)
        session = object()
        self.sessions[source_name] = session
        return session

    async def refresh_tools(self, source_name: str) -> dict[str, Any]:
        self.refreshed_sources.append(source_name)
        return dict(self.cached_tools.get(source_name, {}))

    def list_cached_tools(self, source_name: str) -> dict[str, Any]:
        return dict(self.cached_tools.get(source_name, {}))

    def list_tool_cache(self) -> dict[str, dict[str, Any]]:
        return {name: dict(items) for name, items in self.cached_tools.items()}

    def list_prompt_sources(self) -> list[str]:
        return []

    def list_resource_sources(self) -> list[str]:
        return []

    def build_node_tools(
        self,
        prj,
        selectors: list[vocode_settings.MCPToolSelector],
        disabled_selectors: list[vocode_settings.MCPToolSelector],
        *,
        resolution_mode: str,
        hide_listed_tools: bool,
    ) -> tuple[dict[str, Any], dict[str, vocode_settings.ToolSpec]]:
        return mcp_tool_materialization.build_node_tools(
            self,
            prj,
            selectors,
            disabled_selectors,
            resolution_mode=resolution_mode,
            hide_listed_tools=hide_listed_tools,
        )

    async def apply_workflow_requirements(
        self,
        workflow_execution_id: str,
        source_names: list[str],
    ):
        return await self.apply_node_requirements(
            workflow_execution_id,
            "__workflow__",
            source_names,
        )

    async def apply_node_requirements(
        self,
        workflow_execution_id: str,
        owner_id: str,
        source_names: list[str],
    ):
        self.applied_workflow_requirements.append(
            (workflow_execution_id, list(source_names))
        )
        owner_key = (workflow_execution_id, owner_id)
        current_sources = self.workflow_sources_by_owner.setdefault(owner_key, set())
        started_sources: List[str] = []
        for source_name in source_names:
            if source_name in current_sources:
                continue
            owners = self.workflow_owners_by_source.setdefault(source_name, set())
            should_start = not owners
            owners.add(owner_key)
            current_sources.add(source_name)
            if should_start:
                await self.start_session(source_name)
                started_sources.append(source_name)
        return MCPWorkflowSessionChange(
            started_sources=started_sources,
            stopped_sources=[],
        )

    async def clear_workflow_requirements(self, workflow_execution_id: str):
        return await self.clear_node_requirements(
            workflow_execution_id,
            "__workflow__",
        )

    async def clear_node_requirements(
        self,
        workflow_execution_id: str,
        owner_id: str,
    ):
        self.cleared_workflow_requirements.append(workflow_execution_id)
        owner_key = (workflow_execution_id, owner_id)
        source_names = self.workflow_sources_by_owner.pop(owner_key, set())
        stopped_sources: List[str] = []
        for source_name in source_names:
            owners = self.workflow_owners_by_source.get(source_name)
            if owners is None:
                continue
            owners.discard(owner_key)
            if owners:
                continue
            self.workflow_owners_by_source.pop(source_name, None)
            await self.close_session(source_name)
            stopped_sources.append(source_name)
        return MCPWorkflowSessionChange(
            started_sources=[],
            stopped_sources=stopped_sources,
        )

    async def close_session(self, source_name: str) -> None:
        self.closed_sources.append(source_name)
        self.sessions.pop(source_name, None)

    async def call_tool(
        self,
        source_name: str,
        tool_name: str,
        arguments: Optional[dict[str, object]] = None,
    ) -> dict[str, object]:
        _ = arguments
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"{source_name}:{tool_name}",
                }
            ],
            "isError": False,
        }


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
    executor.bind_run_context("run-1", "wf")

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
async def test_llm_executor_init_starts_node_mcp_sources_and_refreshes_cache() -> None:
    project = StubProject(
        settings=vocode_settings.Settings(
            mcp=vocode_settings.MCPSettings(
                sources={
                    "local": vocode_settings.MCPStdioSourceSettings(
                        command="uvx",
                    )
                }
            )
        )
    )
    project.mcp = FakeMCPService()  # type: ignore[assignment]
    node = LLMNode(
        name="node-mcp-init",
        type="llm",
        model="gpt-3.5-turbo",
        mcp=LLMNodeMCPSettings(
            tools=[vocode_settings.MCPToolSelector(source="local", tool="*")]
        ),
    )
    executor = LLMExecutor(config=node, project=project)
    executor.bind_run_context("run-1", "wf")

    await executor.init()

    assert project.mcp.applied_workflow_requirements == [("run-1", ["local"])]
    assert project.mcp.started_sources == ["local"]
    assert project.mcp.refreshed_sources == ["local"]

    await executor.shutdown()

    assert project.mcp.cleared_workflow_requirements == ["run-1"]
    assert project.mcp.closed_sources == ["local"]


@pytest.mark.asyncio
async def test_llm_executors_share_workflow_scoped_mcp_session_per_run() -> None:
    project = StubProject(
        settings=vocode_settings.Settings(
            mcp=vocode_settings.MCPSettings(
                sources={
                    "local": vocode_settings.MCPStdioSourceSettings(
                        command="uvx",
                    )
                }
            )
        )
    )
    project.mcp = FakeMCPService()  # type: ignore[assignment]
    first_node = LLMNode(
        name="node-mcp-shared-a",
        type="llm",
        model="gpt-3.5-turbo",
        mcp=LLMNodeMCPSettings(
            tools=[vocode_settings.MCPToolSelector(source="local", tool="*")]
        ),
    )
    second_node = LLMNode(
        name="node-mcp-shared-b",
        type="llm",
        model="gpt-3.5-turbo",
        mcp=LLMNodeMCPSettings(
            tools=[vocode_settings.MCPToolSelector(source="local", tool="*")]
        ),
    )

    first_executor = LLMExecutor(config=first_node, project=project)
    second_executor = LLMExecutor(config=second_node, project=project)
    first_executor.bind_run_context("run-1", "wf")
    second_executor.bind_run_context("run-1", "wf")

    await first_executor.init()
    await second_executor.init()

    assert project.mcp.started_sources == ["local"]
    assert project.mcp.applied_workflow_requirements == [
        ("run-1", ["local"]),
        ("run-1", ["local"]),
    ]

    await first_executor.shutdown()

    assert project.mcp.closed_sources == []

    await second_executor.shutdown()

    assert project.mcp.closed_sources == ["local"]


@pytest.mark.asyncio
async def test_llm_executor_init_adds_node_local_mcp_tools() -> None:
    project = StubProject(
        settings=vocode_settings.Settings(
            mcp=vocode_settings.MCPSettings(
                sources={
                    "local": vocode_settings.MCPStdioSourceSettings(
                        command="uvx",
                    )
                }
            )
        )
    )
    project.mcp = FakeMCPService()  # type: ignore[assignment]
    project.mcp.sessions["local"] = object()
    project.mcp.cached_tools["local"] = {
        "echo": mcp_models.MCPToolDescriptor(
            source_name="local",
            tool_name="echo",
            title=None,
            description="Echo tool",
            input_schema={"type": "object", "properties": {}},
        )
    }

    node = LLMNode(
        name="node-mcp-tools",
        type="llm",
        model="gpt-3.5-turbo",
        mcp=LLMNodeMCPSettings(
            tools=[vocode_settings.MCPToolSelector(source="local", tool="echo")]
        ),
    )
    executor = LLMExecutor(config=node, project=project)
    executor.bind_run_context("run-1", "wf")

    await executor.init()

    tools = await executor._build_connect_tools({})

    assert tools is not None
    assert [tool.name for tool in tools] == [
        mcp_naming.build_internal_tool_name("local", "echo")
    ]


@pytest.mark.asyncio
async def test_runner_executes_node_local_mcp_tool() -> None:
    project = StubProject(
        settings=vocode_settings.Settings(
            mcp=vocode_settings.MCPSettings(
                sources={
                    "local": vocode_settings.MCPStdioSourceSettings(
                        command="uvx",
                    )
                }
            )
        )
    )
    project.mcp = FakeMCPService()  # type: ignore[assignment]
    project.mcp.sessions["local"] = object()
    project.mcp.cached_tools["local"] = {
        "echo": mcp_models.MCPToolDescriptor(
            source_name="local",
            tool_name="echo",
            title=None,
            description="Echo tool",
            input_schema={"type": "object", "properties": {}},
        )
    }

    node = LLMNode(
        name="node-mcp-tools",
        type="llm",
        model="gpt-3.5-turbo",
        mcp=LLMNodeMCPSettings(
            tools=[vocode_settings.MCPToolSelector(source="local", tool="echo")]
        ),
    )
    workflow = Workflow(
        name="wf",
        graph=models.Graph(nodes=[node], edges=[]),
    )
    runner = Runner(workflow, project, None)
    executor = runner._executors[node.name]
    assert isinstance(executor, LLMExecutor)
    await executor.init()

    tool_name = mcp_naming.build_internal_tool_name("local", "echo")
    execution = state.NodeExecution(
        workflow_execution=runner.execution,
        node=node.name,
        status=state.RunStatus.RUNNING,
    )
    execution = project.history.upsert_node_execution(runner.execution, execution)
    result = await runner._execute_tool_call(
        state.ToolCallReq(
            id="call-local-mcp-tool",
            name=tool_name,
            arguments={"value": "hello"},
        ),
        execution,
    )

    assert result.kind == "response"
    response = result.response
    assert response.status == state.ToolCallStatus.COMPLETED
    assert response.result == {
        "content": [{"type": "text", "text": "local:echo"}],
        "isError": False,
        "text": "local:echo",
    }

    await executor.shutdown()


@pytest.mark.asyncio
async def test_llm_executor_outcome_tag_selection_strips_only_trailing_valid_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: FakeAsyncLLMClient(
            _stream_with_text(
                "Earlier reference\nOUTCOME: maybe\nFinal answer\nOUTCOME: success"
            ),
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-outcome-tag-tight-strip",
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

    run, execution = _build_execution_with_input(
        "node-outcome-tag-tight-strip",
        "Hi",
    )
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    final_message_step = [s for s in steps if s.type == state.StepType.OUTPUT_MESSAGE][
        -1
    ]
    assert final_message_step.message is not None
    assert (
        final_message_step.message.text
        == "Earlier reference\nOUTCOME: maybe\nFinal answer"
    )
    assert final_message_step.outcome_name == "success"


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
    assert first_step.llm_usage is None

    async for _ in agen:
        pass

    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_llm_executor_preview_usage_uses_last_real_message_usage_from_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}

    def fake_client_factory(*args, **kwargs) -> FakeAsyncLLMClient:
        call_count["n"] += 1
        return FakeAsyncLLMClient(_stream_with_text("preview response"), **kwargs)

    monkeypatch.setattr(connect, "AsyncLLMClient", fake_client_factory)

    project = StubProject()
    node = LLMNode(
        name="node-preview-estimate",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-preview-estimate",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    first_user = state.Message(role=models.Role.USER, text="12345678")
    assistant = state.Message(
        role=models.Role.ASSISTANT,
        text="done",
        llm_usage=state.LLMUsageStats(
            prompt_tokens=10,
            completion_tokens=3,
            cost_dollars=0.2,
        ),
    )
    trailing_user = state.Message(role=models.Role.USER, text="1234")
    for message in [first_user, assistant, trailing_user]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=first_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=assistant.id,
            llm_usage=assistant.llm_usage,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=trailing_user.id,
            is_complete=True,
        ),
    )

    agen = executor.run(ExecutorInput(execution=execution, run=run))
    first_step = await agen.__anext__()

    assert call_count["n"] == 0
    assert first_step.llm_usage is not None
    assert first_step.llm_usage.prompt_tokens == 10
    assert first_step.llm_usage.completion_tokens == 3
    assert first_step.llm_usage.cost_dollars == 0.2
    assert first_step.llm_usage.model_name == "gpt-3.5-turbo"

    async for _ in agen:
        pass


def test_should_trigger_compaction_uses_percentage_threshold() -> None:
    settings = node = LLMNode(
        name="node-compaction-threshold",
        type="llm",
        model="gpt-3.5-turbo",
    ).compaction

    assert should_trigger_compaction(settings, 1000, 699) is False
    assert should_trigger_compaction(settings, 1000, 700) is True
    assert should_trigger_compaction(settings, None, 900) is False


def test_should_trigger_compaction_respects_disabled_setting() -> None:
    settings = LLMNode(
        name="node-compaction-disabled",
        type="llm",
        model="gpt-3.5-turbo",
    ).compaction.model_copy(update={"enabled": False})

    assert should_trigger_compaction(settings, 1000, 1000) is False


def test_llm_executor_prepare_compaction_reports_threshold_state() -> None:
    project = StubProject()
    node = LLMNode(
        name="node-prepare-compaction",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        extra={"model_max_tokens": 20},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-prepare-compaction",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    user_message = state.Message(role=models.Role.USER, text="x" * 60)
    history.upsert_message(run, user_message)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=user_message.id,
            is_complete=True,
        ),
    )

    result = executor._prepare_compaction(ExecutorInput(execution=execution, run=run))

    assert result.prompt_messages_count == 1
    assert result.estimated_context_tokens == 15
    assert result.input_token_limit == 20
    assert result.threshold_context_tokens == 15
    assert result.should_compact is True


def test_llm_executor_prepare_compaction_respects_compaction_boundary() -> None:
    project = StubProject()
    node = LLMNode(
        name="node-prepare-compaction-boundary",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        extra={"model_max_tokens": 100},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-prepare-compaction-boundary",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 200)
    summary = state.Message(
        role=models.Role.SYSTEM,
        text="summary",
        state=CompactionSummaryState(
            compacted_step_ids=[],
            compacted_message_ids=[old_user.id],
            tokens_before=100,
            tokens_after_estimate=2,
            trigger_threshold_ratio=0.5,
        ),
    )
    recent_user = state.Message(role=models.Role.USER, text="tail")
    for message in [old_user, summary, recent_user]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=summary.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    result = executor._prepare_compaction(ExecutorInput(execution=execution, run=run))

    assert result.prompt_messages_count == 2
    assert result.estimated_context_tokens == 3
    assert result.threshold_context_tokens == 3
    assert result.should_compact is False


@pytest.mark.asyncio
async def test_llm_executor_persists_compaction_step_before_request(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    captured_requests: List[Any] = []
    response = _assistant_response("after compaction")

    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: RecordingAsyncLLMClient(
            [
                FakeStreamHandle(
                    [
                        connect.TextDeltaEvent(index=0, delta="after compaction"),
                        connect.ResponseEndEvent(response=response),
                    ],
                    final_response=response,
                )
            ],
            captured_requests,
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-persist-compaction",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        extra={"model_max_tokens": 8},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-persist-compaction",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 60)
    recent_user = state.Message(role=models.Role.USER, text="tail")
    recent_assistant = state.Message(role=models.Role.ASSISTANT, text="tail-2")
    for message in [old_user, recent_user, recent_assistant]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=recent_assistant.id,
            is_complete=True,
        ),
    )

    with caplog.at_level(logging.INFO, logger="vocode"):
        steps: List[state.Step] = []
        async for step in executor.run(ExecutorInput(execution=execution, run=run)):
            steps.append(step)

    compaction_steps = [
        step
        for step in execution.iter_steps()
        if step.type == state.StepType.CONTEXT_COMPACTION
    ]
    assert len(compaction_steps) == 1
    compaction_step = compaction_steps[0]
    assert compaction_step.message is not None
    assert (
        "The conversation history before this point was compacted"
        in compaction_step.message.text
    )
    assert compaction_step.message.state is not None
    summary_state = CompactionSummaryState.model_validate(
        compaction_step.message.state.model_dump(mode="python")
    )
    assert summary_state.summary_input_tokens == 5
    assert summary_state.summary_output_tokens == 16
    assert execution.state is not None
    assert isinstance(execution.state, LLMExecutionState)
    assert execution.state.compaction is not None
    assert (
        execution.state.compaction.latest_compaction_message_id
        == compaction_step.message_id
    )
    assert execution.state.compaction.compaction_count == 1
    compaction_logs = [
        record
        for record in caplog.records
        if "Context compaction finished" in record.getMessage()
    ]
    assert len(compaction_logs) == 1
    log_message = compaction_logs[0].getMessage()
    assert "status" in log_message
    assert "completed" in log_message
    assert "prompt_messages_before" in log_message
    assert "prompt_messages_after" in log_message
    assert "summary_input_tokens" in log_message
    assert "summary_output_tokens" in log_message

    assert len(captured_requests) == 2
    summary_request = captured_requests[0]
    assert "Transcript to compact:" in summary_request.messages[0].content
    request = captured_requests[1]
    assert request.system_prompt is not None
    assert "<summary>" in request.system_prompt
    assert len(request.messages) == 2
    assert isinstance(request.messages[0], connect.UserMessage)
    assert request.messages[0].content == "tail"
    assert isinstance(request.messages[1], connect.AssistantMessage)
    assert request.messages[1].content[0].text == "tail-2"


def test_llm_executor_build_connect_messages_uses_persisted_compaction_step() -> None:
    project = StubProject()
    node = LLMNode(
        name="node-build-after-compaction",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-build-after-compaction",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
            state=LLMExecutionCompactionState(compaction_count=1),
        ),
    )

    summary = state.Message(role=models.Role.SYSTEM, text="persisted summary")
    recent_user = state.Message(role=models.Role.USER, text="recent user")
    for message in [summary, recent_user]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=summary.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    inp = ExecutorInput(execution=execution, run=run)
    system_prompt, messages = executor.build_connect_messages(
        executor._iter_prompt_messages(inp)
    )

    assert system_prompt is not None
    assert "persisted summary" in system_prompt
    assert len(messages) == 1
    assert isinstance(messages[0], connect.UserMessage)
    assert messages[0].content == "recent user"


def test_compaction_transcript_serializes_tool_calls_and_results() -> None:
    message = state.Message(
        role=models.Role.ASSISTANT,
        text="I will inspect the repo",
        thinking_content="Need to inspect llm files",
        tool_call_requests=[
            state.ToolCallReq(
                id="call-1",
                name="read_files",
                arguments={"path": "src/vocode/runner/executors/llm/llm.py"},
            )
        ],
        tool_call_responses=[
            state.ToolCallResp(
                id="call-1",
                name="read_files",
                status=state.ToolCallStatus.COMPLETED,
                result={"error": "context overflow"},
            )
        ],
    )

    transcript = serialize_messages_to_transcript([message])

    assert "[Assistant]" in transcript
    assert "I will inspect the repo" in transcript
    assert "[Assistant thinking]" in transcript
    assert "Need to inspect llm files" in transcript
    assert "[Assistant tool calls]" in transcript
    assert (
        '- read_files({"path": "src/vocode/runner/executors/llm/llm.py"})' in transcript
    )
    assert "[Tool results]" in transcript
    assert '- read_files: {"error": "context overflow"}' in transcript


def test_compaction_instruction_builder_appends_custom_instructions() -> None:
    instructions = build_compaction_instructions(
        "Preserve exact file paths and tool names."
    )

    assert "Return markdown with exactly these sections" in instructions
    assert "Preserve exact file paths and tool names." in instructions


def test_build_summary_generation_prompt_includes_previous_summary_and_transcript() -> (
    None
):
    settings = CompactionSettings(prompt_instructions="Keep exact paths.")

    prompt = build_summary_generation_prompt(
        "## Goal\nEarlier summary",
        "[User]: inspect src/app.py",
        settings,
    )

    assert "Keep exact paths." in prompt
    assert "Prioritize these details when present:" in prompt
    assert "Update the existing summary instead of rewriting from scratch." in prompt
    assert "Previous summary:" in prompt
    assert "Earlier summary" in prompt
    assert "Transcript to compact:" in prompt
    assert "src/app.py" in prompt


def test_compaction_prompt_resolvers_use_node_overrides() -> None:
    settings = CompactionSettings(
        prompt_system="Custom compaction system",
        prompt_instructions="Keep exact paths.",
    )

    assert resolve_compaction_system_prompt(settings) == "Custom compaction system"
    resolved_instructions = resolve_compaction_instructions(settings)
    assert "Return markdown with exactly these sections" in resolved_instructions
    assert "Keep exact paths." in resolved_instructions


def test_select_compaction_cut_index_prefers_user_boundary() -> None:
    execution_id = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    ).id
    prompt_messages = [
        (state.Message(role=models.Role.USER, text="a" * 8), None),
        (
            state.Message(role=models.Role.ASSISTANT, text="b" * 8),
            state.Step(execution_id=execution_id, type=state.StepType.OUTPUT_MESSAGE),
        ),
        (
            state.Message(role=models.Role.USER, text="c" * 8),
            state.Step(execution_id=execution_id, type=state.StepType.INPUT_MESSAGE),
        ),
        (
            state.Message(role=models.Role.ASSISTANT, text="d" * 8),
            state.Step(execution_id=execution_id, type=state.StepType.OUTPUT_MESSAGE),
        ),
    ]

    cut_index = select_compaction_cut_index(
        prompt_messages,
        CompactionSettings(keep_recent_ratio=0.25),
        8,
    )

    assert cut_index == 2


def test_select_compaction_cut_index_falls_back_to_assistant_boundary() -> None:
    execution_id = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    ).id
    prompt_messages = [
        (
            state.Message(role=models.Role.ASSISTANT, text="a" * 8),
            state.Step(execution_id=execution_id, type=state.StepType.OUTPUT_MESSAGE),
        ),
        (
            state.Message(role=models.Role.ASSISTANT, text="b" * 8),
            state.Step(execution_id=execution_id, type=state.StepType.OUTPUT_MESSAGE),
        ),
    ]

    cut_index = select_compaction_cut_index(
        prompt_messages,
        CompactionSettings(keep_recent_ratio=0.25),
        8,
    )

    assert cut_index == 1


def test_select_compaction_cut_index_keeps_tool_result_with_preceding_request() -> None:
    execution_id = state.NodeExecution(
        node="node",
        status=state.RunStatus.RUNNING,
    ).id
    prompt_messages = [
        (
            state.Message(
                role=models.Role.ASSISTANT,
                text="call tool",
                tool_call_requests=[
                    state.ToolCallReq(
                        id="call-1",
                        name="read_files",
                        arguments={"path": "a.py"},
                    )
                ],
            ),
            state.Step(execution_id=execution_id, type=state.StepType.OUTPUT_MESSAGE),
        ),
        (
            state.Message(
                role=models.Role.ASSISTANT,
                text="",
                tool_call_responses=[
                    state.ToolCallResp(
                        id="call-1",
                        name="read_files",
                        status=state.ToolCallStatus.COMPLETED,
                        result={"ok": True},
                    )
                ],
            ),
            state.Step(execution_id=execution_id, type=state.StepType.OUTPUT_MESSAGE),
        ),
    ]

    cut_index = select_compaction_cut_index(
        prompt_messages,
        CompactionSettings(keep_recent_ratio=0.25),
        8,
    )

    assert cut_index == 1


@pytest.mark.asyncio
async def test_llm_executor_compaction_summary_uses_structured_sections_and_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_requests: List[Any] = []
    stream_requests: List[Any] = []
    summary_response = _assistant_response(
        "## Goal\nContinue task\n\n## Constraints & Preferences\n- Keep exact paths.\n\n## Progress\n### Done\n- Read src/vocode/runner/executors/llm/llm.py\n### In Progress\n- Compact context\n### Blocked\n- context overflow\n\n## Key Decisions\n- Use compaction\n\n## Next Steps\n- Continue from tail\n\n## Critical Context\n- Tool: read_files"
    )
    response = _assistant_response("after compaction")
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: SummaryThenStreamAsyncLLMClient(
            summary_response,
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="after compaction"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            summary_requests,
            stream_requests,
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-structured-compaction",
        type="llm",
        model="gpt-3.5-turbo",
        extra={"model_max_tokens": 20},
        compaction=LLMNode(
            name="nested-unused",
            type="llm",
            model="gpt-3.5-turbo",
        ).compaction.model_copy(
            update={
                "prompt_system": "Custom compaction system",
                "prompt_instructions": "Keep exact tool names.",
            }
        ),
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-structured-compaction",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    assistant = state.Message(
        role=models.Role.ASSISTANT,
        text="Calling read_files on src/vocode/runner/executors/llm/llm.py",
        tool_call_requests=[
            state.ToolCallReq(
                id="call-1",
                name="read_files",
                arguments={"path": "src/vocode/runner/executors/llm/llm.py"},
            )
        ],
        tool_call_responses=[
            state.ToolCallResp(
                id="call-1",
                name="read_files",
                status=state.ToolCallStatus.COMPLETED,
                result={"error": "context overflow"},
            )
        ],
    )
    recent_user = state.Message(role=models.Role.USER, text="tail")
    for message in [assistant, recent_user]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=assistant.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    async for _ in executor.run(ExecutorInput(execution=execution, run=run)):
        pass

    assert len(summary_requests) == 1
    assert "Transcript to compact:" in summary_requests[0].messages[0].content
    assert "read_files" in summary_requests[0].messages[0].content

    compaction_step = next(
        step
        for step in execution.iter_steps()
        if step.type == state.StepType.CONTEXT_COMPACTION
    )

    assert compaction_step.message is not None
    assert "## Goal" in compaction_step.message.text
    assert "## Constraints & Preferences" in compaction_step.message.text
    assert "## Progress" in compaction_step.message.text
    assert "## Key Decisions" in compaction_step.message.text
    assert "## Next Steps" in compaction_step.message.text
    assert "## Critical Context" in compaction_step.message.text
    assert "read_files" in compaction_step.message.text
    assert "<compaction_prompt_system>" not in compaction_step.message.text
    assert "<compaction_prompt_instructions>" not in compaction_step.message.text


@pytest.mark.asyncio
async def test_llm_executor_repeated_compaction_uses_update_mode_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_requests: List[Any] = []
    stream_requests: List[Any] = []
    summary_response = _assistant_response(
        "## Goal\nUpdated task\n\n## Constraints & Preferences\n- Keep exact paths.\n\n## Progress\n### Done\n- Preserved prior summary\n### In Progress\n- Continue task\n### Blocked\n- None\n\n## Key Decisions\n- Keep prior facts\n\n## Next Steps\n- Continue\n\n## Critical Context\n- Tool: read_files"
    )
    response = _assistant_response("after repeated compaction")
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: SummaryThenStreamAsyncLLMClient(
            summary_response,
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="after repeated compaction"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            summary_requests,
            stream_requests,
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-repeated-compaction",
        type="llm",
        model="gpt-3.5-turbo",
        extra={"model_max_tokens": 20},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-repeated-compaction",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    prior_summary = state.Message(
        role=models.Role.SYSTEM,
        text=(
            "The conversation history before this point was compacted into the following summary:\n\n"
            "<summary>\n## Goal\nEarlier summary\n</summary>"
        ),
    )
    old_user = state.Message(role=models.Role.USER, text="x" * 60)
    recent_user = state.Message(role=models.Role.USER, text="tail")
    for message in [prior_summary, old_user, recent_user]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=prior_summary.id,
            state=CompactionSummaryState(
                compacted_step_ids=[],
                tokens_before=10,
                tokens_after_estimate=5,
                trigger_threshold_ratio=0.5,
            ),
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    async for _ in executor.run(ExecutorInput(execution=execution, run=run)):
        pass

    assert len(summary_requests) == 1
    summary_prompt = summary_requests[0].messages[0].content
    assert (
        "Update the existing summary instead of rewriting from scratch."
        in summary_prompt
    )
    assert "Previous summary:" in summary_prompt
    assert "Earlier summary" in summary_prompt


def test_should_trigger_compaction_uses_last_message_usage_when_available() -> None:
    settings = LLMNode(
        name="node-compaction-last-message-usage",
        type="llm",
        model="gpt-3.5-turbo",
    ).compaction

    assert should_trigger_compaction(settings, 1000, 700) is True
    assert should_trigger_compaction(settings, 1000, 699) is False


@pytest.mark.asyncio
async def test_llm_executor_prepare_compaction_uses_last_message_usage_for_threshold() -> (
    None
):
    project = StubProject()
    node = LLMNode(
        name="node-prepare-compaction-last-message-usage",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        extra={"model_max_tokens": 100},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-prepare-compaction-last-message-usage",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 20)
    assistant = state.Message(
        role=models.Role.ASSISTANT,
        text="reply",
        llm_usage=state.LLMUsageStats(
            prompt_tokens=71,
            completion_tokens=3,
        ),
    )
    trailing_user = state.Message(role=models.Role.USER, text="tail")
    for message in [old_user, assistant, trailing_user]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=assistant.id,
            llm_usage=assistant.llm_usage,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=trailing_user.id,
            is_complete=True,
        ),
    )

    result = executor._prepare_compaction(ExecutorInput(execution=execution, run=run))

    assert result.estimated_context_tokens == 8
    assert result.threshold_context_tokens == 71
    assert result.should_compact is True


@pytest.mark.asyncio
async def test_llm_executor_logs_realized_compaction_savings_from_actual_usage(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    summary_requests: List[Any] = []
    stream_requests: List[Any] = []
    summary_response = connect.AssistantMessage(
        provider="openai",
        model="gpt-3.5-turbo",
        api_family="openai-responses",
        content=[connect.TextBlock(text="condensed summary")],
        finish_reason="stop",
        usage=connect.Usage(
            input_tokens=9,
            output_tokens=4,
            total_tokens=13,
            completeness="final",
        ),
    )
    response = connect.AssistantMessage(
        provider="openai",
        model="gpt-3.5-turbo",
        api_family="openai-responses",
        content=[connect.TextBlock(text="after compaction")],
        finish_reason="stop",
        usage=connect.Usage(
            input_tokens=20,
            output_tokens=6,
            total_tokens=26,
            completeness="final",
        ),
    )
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: SummaryThenStreamAsyncLLMClient(
            summary_response,
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="after compaction"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            summary_requests,
            stream_requests,
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-realized-compaction-savings",
        type="llm",
        model="gpt-3.5-turbo",
        extra={"model_max_tokens": 50},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-realized-compaction-savings",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 120)
    assistant = state.Message(
        role=models.Role.ASSISTANT,
        text="older assistant",
        llm_usage=state.LLMUsageStats(
            prompt_tokens=71,
            completion_tokens=3,
        ),
    )
    trailing_user = state.Message(role=models.Role.USER, text="tail")
    for message in [old_user, assistant, trailing_user]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=assistant.id,
            llm_usage=assistant.llm_usage,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=trailing_user.id,
            is_complete=True,
        ),
    )

    with caplog.at_level(logging.INFO, logger="vocode"):
        async for _ in executor.run(ExecutorInput(execution=execution, run=run)):
            pass

    realized_logs = [
        record
        for record in caplog.records
        if "Context compaction realized" in record.getMessage()
    ]
    assert len(realized_logs) == 1
    realized_message = realized_logs[0].getMessage()
    assert "prompt_tokens_before" in realized_message
    assert "71" in realized_message
    assert "prompt_tokens_after" in realized_message
    assert "20" in realized_message
    assert "saved_input_tokens" in realized_message
    assert "51" in realized_message
    assert "summary_input_tokens" in realized_message
    assert "9" in realized_message


@pytest.mark.asyncio
async def test_llm_executor_logs_negative_realized_compaction_savings(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    summary_requests: List[Any] = []
    stream_requests: List[Any] = []
    summary_response = connect.AssistantMessage(
        provider="openai",
        model="gpt-3.5-turbo",
        api_family="openai-responses",
        content=[connect.TextBlock(text="condensed summary")],
        finish_reason="stop",
        usage=connect.Usage(
            input_tokens=9,
            output_tokens=4,
            total_tokens=13,
            completeness="final",
        ),
    )
    response = connect.AssistantMessage(
        provider="openai",
        model="gpt-3.5-turbo",
        api_family="openai-responses",
        content=[connect.TextBlock(text="after compaction")],
        finish_reason="stop",
        usage=connect.Usage(
            input_tokens=20,
            output_tokens=6,
            total_tokens=26,
            completeness="final",
        ),
    )
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: SummaryThenStreamAsyncLLMClient(
            summary_response,
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="after compaction"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            summary_requests,
            stream_requests,
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-negative-realized-compaction-savings",
        type="llm",
        model="gpt-3.5-turbo",
        extra={"model_max_tokens": 10},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-negative-realized-compaction-savings",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 120)
    assistant = state.Message(
        role=models.Role.ASSISTANT,
        text="older assistant",
        llm_usage=state.LLMUsageStats(
            prompt_tokens=12,
            completion_tokens=3,
        ),
    )
    trailing_user = state.Message(role=models.Role.USER, text="tail")
    for message in [old_user, assistant, trailing_user]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=assistant.id,
            llm_usage=assistant.llm_usage,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=trailing_user.id,
            is_complete=True,
        ),
    )

    with caplog.at_level(logging.INFO, logger="vocode"):
        async for _ in executor.run(ExecutorInput(execution=execution, run=run)):
            pass

    realized_logs = [
        record
        for record in caplog.records
        if "Context compaction realized" in record.getMessage()
    ]
    assert len(realized_logs) == 1
    realized_message = realized_logs[0].getMessage()
    assert "prompt_tokens_before" in realized_message
    assert "12" in realized_message
    assert "prompt_tokens_after" in realized_message
    assert "20" in realized_message
    assert "saved_input_tokens" in realized_message
    assert "-8" in realized_message


@pytest.mark.asyncio
async def test_llm_executor_preview_usage_ignores_run_last_step_stats_without_lineage_usage(
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
    assert first_step.llm_usage is None

    async for _ in agen:
        pass

    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_llm_executor_preview_usage_uses_last_tool_round_message_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}

    def fake_client_factory(*args, **kwargs) -> FakeAsyncLLMClient:
        call_count["n"] += 1
        return FakeAsyncLLMClient(_stream_with_text("preview response"), **kwargs)

    monkeypatch.setattr(connect, "AsyncLLMClient", fake_client_factory)

    project = StubProject()
    node = LLMNode(
        name="node-preview-tool-round-usage",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-preview-tool-round-usage",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    assistant = state.Message(
        role=models.Role.ASSISTANT,
        text="tool round",
        tool_call_requests=[
            state.ToolCallReq(id="call-1", name="echo", arguments={"x": 1})
        ],
        tool_call_responses=[
            state.ToolCallResp(
                id="call-1",
                name="echo",
                status=state.ToolCallStatus.COMPLETED,
                result={"ok": True},
            )
        ],
        llm_usage=state.LLMUsageStats(
            prompt_tokens=33,
            completion_tokens=7,
            cost_dollars=0.5,
        ),
    )
    trailing_user = state.Message(role=models.Role.USER, text="continue")
    for message in [assistant, trailing_user]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=assistant.id,
            llm_usage=assistant.llm_usage,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=trailing_user.id,
            is_complete=True,
        ),
    )

    agen = executor.run(ExecutorInput(execution=execution, run=run))
    first_step = await agen.__anext__()

    assert call_count["n"] == 0
    assert first_step.llm_usage is not None
    assert first_step.llm_usage.prompt_tokens == 33
    assert first_step.llm_usage.completion_tokens == 7
    assert first_step.llm_usage.cost_dollars == 0.5
    assert first_step.llm_usage.model_name == "gpt-3.5-turbo"

    steps = [first_step]
    async for step in agen:
        steps.append(step)

    assert call_count["n"] == 1
    final_step = [step for step in steps if step.type == state.StepType.OUTPUT_MESSAGE][
        -1
    ]
    assert final_step.llm_usage is not None
    assert final_step.llm_usage.prompt_tokens == 5
    assert final_step.llm_usage.completion_tokens == len("preview response")


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
async def test_llm_executor_build_tools_omits_skip_listing_tools() -> None:
    project = StubProject()

    class EchoTool:
        async def openapi_spec(
            self,
            spec: vocode_settings.ToolSpec,
        ) -> dict[str, Any]:
            return {
                "name": spec.name,
                "parameters": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
            }

        async def run(
            self,
            spec: vocode_settings.ToolSpec,
            args: Any,
        ) -> None:
            return None

    project.tools["echo"] = EchoTool()
    project.tools["fetch"] = EchoTool()

    node = LLMNode(
        name="node-tools-skip-listing-filter",
        type="llm",
        model="gpt-3.5-turbo",
        tools=[
            vocode_settings.ToolSpec(
                name="echo",
                enabled=True,
                skip_listing=True,
            ),
            vocode_settings.ToolSpec(
                name="fetch",
                enabled=True,
            ),
        ],
    )

    executor = LLMExecutor(config=node, project=project)
    effective_specs = llm_helpers.build_effective_tool_specs(project, node)

    tools = await executor._build_connect_tools(effective_specs)

    assert tools is not None
    assert [tool.name for tool in tools] == ["fetch"]


def test_build_effective_tool_specs_global_skip_listing_overrides_node() -> None:
    project = StubProject()
    project.settings.tools = [
        vocode_settings.ToolSpec(
            name="echo",
            enabled=True,
            skip_listing=True,
        )
    ]

    node = LLMNode(
        name="node-tools-skip-listing",
        type="llm",
        model="gpt-3.5-turbo",
        tools=[
            vocode_settings.ToolSpec(
                name="echo",
                enabled=True,
                skip_listing=False,
            )
        ],
    )

    effective_specs = llm_helpers.build_effective_tool_specs(project, node)

    assert effective_specs["echo"].skip_listing is True


def test_build_effective_tool_specs_global_skip_listing_false_overrides_node() -> None:
    project = StubProject()
    project.settings.tools = [
        vocode_settings.ToolSpec(
            name="echo",
            enabled=True,
            skip_listing=False,
        )
    ]

    node = LLMNode(
        name="node-tools-skip-listing-global-false",
        type="llm",
        model="gpt-3.5-turbo",
        tools=[
            vocode_settings.ToolSpec(
                name="echo",
                enabled=True,
                skip_listing=True,
            )
        ],
    )

    effective_specs = llm_helpers.build_effective_tool_specs(project, node)

    assert effective_specs["echo"].skip_listing is False


def test_build_effective_tool_specs_keeps_node_skip_listing_without_global() -> None:
    project = StubProject()

    node = LLMNode(
        name="node-tools-skip-listing-local",
        type="llm",
        model="gpt-3.5-turbo",
        tools=[
            vocode_settings.ToolSpec(
                name="echo",
                enabled=True,
                skip_listing=True,
            )
        ],
    )

    effective_specs = llm_helpers.build_effective_tool_specs(project, node)

    assert effective_specs["echo"].skip_listing is True


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
async def test_llm_executor_requires_final_message_after_outcome_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: List[Any] = []
    first_tool_call = connect.ToolCallBlock(
        id="call_outcome_1",
        name=llm_helpers.CHOOSE_OUTCOME_TOOL_NAME,
        arguments={"outcome": "success"},
    )
    first_response = _assistant_response("", tool_calls=[first_tool_call])
    second_response = _assistant_response("Final answer after outcome")
    stream_handles = [
        FakeStreamHandle(
            [connect.ResponseEndEvent(response=first_response)],
            final_response=first_response,
        ),
        FakeStreamHandle(
            [
                connect.TextDeltaEvent(index=0, delta="Final answer after outcome"),
                connect.ResponseEndEvent(response=second_response),
            ],
            final_response=second_response,
        ),
    ]
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: RecordingAsyncLLMClient(
            stream_handles,
            requests,
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-outcome-function-followup",
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

    run, execution = _build_execution_with_input(
        "node-outcome-function-followup",
        "Hi",
    )
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert len(requests) == 2
    second_request = requests[1]
    assert any(
        isinstance(message, connect.ToolResultMessage)
        and message.tool_name == llm_helpers.CHOOSE_OUTCOME_TOOL_NAME
        and message.content[0].text == "outcome accepted"
        for message in second_request.messages
    )

    final_message_step = [s for s in steps if s.type == state.StepType.OUTPUT_MESSAGE][
        -1
    ]
    assert final_message_step.message is not None
    assert final_message_step.message.text == "Final answer after outcome"
    assert final_message_step.outcome_name == "success"


@pytest.mark.asyncio
async def test_llm_executor_retries_when_outcome_tool_call_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: List[Any] = []
    first_tool_call = connect.ToolCallBlock(
        id="call_outcome_invalid",
        name=llm_helpers.CHOOSE_OUTCOME_TOOL_NAME,
        arguments={"outcome": "unknown"},
    )
    first_response = _assistant_response("Bad answer", tool_calls=[first_tool_call])
    second_tool_call = connect.ToolCallBlock(
        id="call_outcome_valid",
        name=llm_helpers.CHOOSE_OUTCOME_TOOL_NAME,
        arguments={"outcome": "success"},
    )
    second_response = _assistant_response(
        "Corrected final answer",
        tool_calls=[second_tool_call],
    )
    stream_handles = [
        FakeStreamHandle(
            [
                connect.TextDeltaEvent(index=0, delta="Bad answer"),
                connect.ResponseEndEvent(response=first_response),
            ],
            final_response=first_response,
        ),
        FakeStreamHandle(
            [
                connect.TextDeltaEvent(index=0, delta="Corrected final answer"),
                connect.ResponseEndEvent(response=second_response),
            ],
            final_response=second_response,
        ),
    ]
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: RecordingAsyncLLMClient(
            stream_handles,
            requests,
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-outcome-function-invalid",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        outcomes=[
            models.OutcomeSlot(name="success"),
            models.OutcomeSlot(name="failure"),
        ],
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input(
        "node-outcome-function-invalid",
        "Hi",
    )
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert len(requests) == 2
    second_request = requests[1]
    assert any(
        isinstance(message, connect.ToolResultMessage)
        and message.content[0].text
        == "invalid outcome, possible options: success, failure"
        for message in second_request.messages
    )
    assert any(
        isinstance(message, connect.UserMessage)
        and "You must provide the final response again AND choose an outcome."
        in message.content
        for message in second_request.messages
    )

    final_message_step = [s for s in steps if s.type == state.StepType.OUTPUT_MESSAGE][
        -1
    ]
    assert final_message_step.message is not None
    assert final_message_step.message.text == "Corrected final answer"
    assert final_message_step.outcome_name == "success"


@pytest.mark.asyncio
async def test_llm_executor_retries_when_tag_outcome_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: List[Any] = []
    first_response = _assistant_response("Tagless answer")
    second_response = _assistant_response("Tagged answer again\nOUTCOME: success")
    stream_handles = [
        FakeStreamHandle(
            [
                connect.TextDeltaEvent(index=0, delta="Tagless answer"),
                connect.ResponseEndEvent(response=first_response),
            ],
            final_response=first_response,
        ),
        FakeStreamHandle(
            [
                connect.TextDeltaEvent(
                    index=0, delta="Tagged answer again\nOUTCOME: success"
                ),
                connect.ResponseEndEvent(response=second_response),
            ],
            final_response=second_response,
        ),
    ]
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: RecordingAsyncLLMClient(
            stream_handles,
            requests,
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-outcome-tag-retry",
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

    run, execution = _build_execution_with_input("node-outcome-tag-retry", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert len(requests) == 2
    assert any(
        isinstance(message, connect.UserMessage)
        and "Append a final line exactly as OUTCOME: <outcome_name>." in message.content
        for message in requests[1].messages
    )

    final_message_step = [s for s in steps if s.type == state.StepType.OUTPUT_MESSAGE][
        -1
    ]
    assert final_message_step.message is not None
    assert final_message_step.message.text == "Tagged answer again"
    assert final_message_step.outcome_name == "success"


@pytest.mark.asyncio
async def test_llm_executor_persists_cached_outcome_across_tool_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tool_call = connect.ToolCallBlock(
        id="call_tool_1",
        name="echo",
        arguments={"x": 1},
    )
    outcome_call = connect.ToolCallBlock(
        id="call_outcome_1",
        name=llm_helpers.CHOOSE_OUTCOME_TOOL_NAME,
        arguments={"outcome": "success"},
    )
    first_response = _assistant_response(
        "Using a tool first",
        tool_calls=[tool_call, outcome_call],
    )
    second_response = _assistant_response("Final answer after tool")
    responses = [first_response, second_response]

    def fake_client_factory(*args, **kwargs) -> FakeAsyncLLMClient:
        if not responses:
            raise RuntimeError("missing response")
        response = responses.pop(0)
        events: List[connect.StreamEvent] = [
            connect.ResponseEndEvent(response=response)
        ]
        if (
            response.content
            and response.content[0].type == "text"
            and response.content[0].text
        ):
            events.insert(
                0, connect.TextDeltaEvent(index=0, delta=response.content[0].text)
            )
        return FakeAsyncLLMClient(
            FakeStreamHandle(events, final_response=response),
            **kwargs,
        )

    monkeypatch.setattr(connect, "AsyncLLMClient", fake_client_factory)

    project = StubProject()
    node = LLMNode(
        name="node-outcome-tool-round",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        outcomes=[
            models.OutcomeSlot(name="success"),
            models.OutcomeSlot(name="failure"),
        ],
        tools=[vocode_settings.ToolSpec(name="echo", enabled=True)],
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-outcome-tool-round", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    first_steps: List[state.Step] = []
    async for step in executor.run(inp):
        first_steps.append(step)

    first_final = [s for s in first_steps if s.type == state.StepType.OUTPUT_MESSAGE][
        -1
    ]
    assert first_final.message is not None
    assert len(first_final.message.tool_call_requests) == 1
    assert first_final.message.tool_call_requests[0].name == "echo"
    assert first_final.outcome_name == "success"
    assert execution.state is not None

    tool_resp = state.ToolCallResp(
        id="call_tool_1",
        name="echo",
        status=state.ToolCallStatus.COMPLETED,
        result={"ok": True},
    )
    first_final.message.tool_call_responses = [tool_resp]
    project.history.upsert_message(run, first_final.message)
    project.history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=first_final.message.id,
            is_complete=True,
            outcome_name=first_final.outcome_name,
        ),
    )

    second_steps: List[state.Step] = []
    async for step in executor.run(inp):
        second_steps.append(step)

    second_final = [s for s in second_steps if s.type == state.StepType.OUTPUT_MESSAGE][
        -1
    ]
    assert second_final.message is not None
    assert second_final.message.text == "Final answer after tool"
    assert second_final.outcome_name == "success"


def test_llm_node_defaults_to_function_outcome_strategy() -> None:
    node = LLMNode(
        name="node-default-outcome-strategy",
        type="llm",
        model="gpt-3.5-turbo",
    )

    assert node.outcome_strategy == models.OutcomeStrategy.FUNCTION


def test_llm_node_defaults_hard_error_retries_to_zero() -> None:
    node = LLMNode(
        name="node-default-hard-error-retries",
        type="llm",
        model="gpt-3.5-turbo",
    )

    assert node.hard_error_retries == 0


def test_llm_node_defaults_max_retries_to_three() -> None:
    node = LLMNode(
        name="node-default-max-retries",
        type="llm",
        model="gpt-3.5-turbo",
    )

    assert node.max_retries == 3


def test_llm_build_system_prompt_uses_custom_outcome_instruction() -> None:
    node = LLMNode(
        name="node-custom-outcome-instruction",
        type="llm",
        model="gpt-3.5-turbo",
        outcomes=[
            models.OutcomeSlot(name="success", description="Works"),
            models.OutcomeSlot(name="failure", description="Fails"),
        ],
        outcome_selection_instruction=(
            "Pick from {outcome_list} using {choose_outcome_tool_name}.\n{outcome_desc_bullets}"
        ),
    )

    system_prompt = llm_helpers.build_system_prompt(node)

    assert system_prompt is not None
    assert "Pick from success, failure using __choose_outcome__." in system_prompt
    assert "- success: Works" in system_prompt


@pytest.mark.asyncio
async def test_llm_executor_uses_custom_missing_outcome_retry_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: List[Any] = []
    first_response = _assistant_response("No outcome yet")
    second_response = _assistant_response("Fixed answer\nOUTCOME: success")
    stream_handles = [
        FakeStreamHandle(
            [
                connect.TextDeltaEvent(index=0, delta="No outcome yet"),
                connect.ResponseEndEvent(response=first_response),
            ],
            final_response=first_response,
        ),
        FakeStreamHandle(
            [
                connect.TextDeltaEvent(index=0, delta="Fixed answer\nOUTCOME: success"),
                connect.ResponseEndEvent(response=second_response),
            ],
            final_response=second_response,
        ),
    ]
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: RecordingAsyncLLMClient(
            stream_handles,
            requests,
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-custom-retry-instruction",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        outcomes=[
            models.OutcomeSlot(name="success"),
            models.OutcomeSlot(name="failure"),
        ],
        outcome_strategy=models.OutcomeStrategy.TAG,
        outcome_retry_instruction="Retry with outcome from {outcome_list}.",
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input(
        "node-custom-retry-instruction",
        "Hi",
    )
    inp = ExecutorInput(execution=execution, run=run)

    async for _ in executor.run(inp):
        pass

    assert any(
        isinstance(message, connect.UserMessage)
        and message.content == "Retry with outcome from success, failure."
        for message in requests[1].messages
    )


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

    assert len(steps) == 2
    assert steps[0].type == state.StepType.OUTPUT_MESSAGE
    assert steps[0].is_complete is False
    rejection = steps[-1]
    assert rejection.type == state.StepType.REJECTION
    assert rejection.message is not None
    assert "provider=chatgpt" in rejection.message.text
    assert "api_family=chatgpt-responses" in rejection.message.text
    assert "status_code=400" in rejection.message.text
    assert "code=provider_error" in rejection.message.text
    assert "retryable=False" in rejection.message.text
    assert '"type": "invalid_request_error"' in rejection.message.text


@pytest.mark.asyncio
async def test_llm_executor_retries_configured_hard_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}
    error = connect.PermanentProviderError(
        connect.ErrorInfo(
            code="provider_error",
            message="Provider request failed",
            provider="chatgpt",
            api_family="chatgpt-responses",
            status_code=400,
            retryable=False,
        )
    )

    def fake_client_factory(*args, **kwargs) -> FakeAsyncLLMClient:
        call_count["n"] += 1
        if call_count["n"] <= 2:
            return FailingAsyncLLMClient(error, **kwargs)
        return FakeAsyncLLMClient(_stream_with_text("Recovered response"), **kwargs)

    monkeypatch.setattr(connect, "AsyncLLMClient", fake_client_factory)

    project = StubProject()
    node = LLMNode(
        name="node-hard-error-retries",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        hard_error_retries=2,
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-hard-error-retries", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert call_count["n"] == 3
    final_step = steps[-1]
    assert final_step.type == state.StepType.OUTPUT_MESSAGE
    assert final_step.message is not None
    assert final_step.message.text == "Recovered response"
    assert final_step.is_complete is True


@pytest.mark.asyncio
async def test_llm_executor_respects_configured_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = {"n": 0}

    async def _fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

    error = connect.RateLimitError(
        connect.ErrorInfo(
            code="rate_limited",
            message="Provider rate limited",
            provider="chatgpt",
            api_family="chatgpt-responses",
            status_code=429,
            retryable=True,
        )
    )

    def fake_client_factory(*args, **kwargs) -> FakeAsyncLLMClient:
        call_count["n"] += 1
        return FailingAsyncLLMClient(error, **kwargs)

    monkeypatch.setattr(connect, "AsyncLLMClient", fake_client_factory)

    project = StubProject()
    node = LLMNode(
        name="node-max-retries-setting",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        max_retries=1,
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-max-retries-setting", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert call_count["n"] == 2
    final_step = steps[-1]
    assert final_step.type == state.StepType.REJECTION
    assert final_step.message is not None
    assert "LLM error:" in final_step.message.text


@pytest.mark.asyncio
async def test_llm_executor_hard_error_retry_streak_resets_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: List[Any] = []
    error = connect.PermanentProviderError(
        connect.ErrorInfo(
            code="provider_error",
            message="Provider request failed",
            provider="chatgpt",
            api_family="chatgpt-responses",
            status_code=400,
            retryable=False,
        )
    )
    first_success = _assistant_response("Missing outcome")
    final_success = _assistant_response("Recovered again\nOUTCOME: success")
    actions: List[Any] = [
        error,
        FakeStreamHandle(
            [
                connect.TextDeltaEvent(index=0, delta="Missing outcome"),
                connect.ResponseEndEvent(response=first_success),
            ],
            final_response=first_success,
        ),
        error,
        FakeStreamHandle(
            [
                connect.TextDeltaEvent(
                    index=0,
                    delta="Recovered again\nOUTCOME: success",
                ),
                connect.ResponseEndEvent(response=final_success),
            ],
            final_response=final_success,
        ),
    ]

    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: SequenceAsyncLLMClient(actions, requests, **kwargs),
    )

    project = StubProject()
    node = LLMNode(
        name="node-hard-error-reset",
        type="llm",
        model="gpt-3.5-turbo",
        system="You are a test assistant.",
        outcomes=[
            models.OutcomeSlot(name="success"),
            models.OutcomeSlot(name="failure"),
        ],
        outcome_strategy=models.OutcomeStrategy.TAG,
        hard_error_retries=1,
    )
    executor = LLMExecutor(config=node, project=project)

    run, execution = _build_execution_with_input("node-hard-error-reset", "Hi")
    inp = ExecutorInput(execution=execution, run=run)

    steps: List[state.Step] = []
    async for step in executor.run(inp):
        steps.append(step)

    assert len(requests) == 4
    final_step = steps[-1]
    assert final_step.type == state.StepType.OUTPUT_MESSAGE
    assert final_step.message is not None
    assert final_step.message.text == "Recovered again"
    assert final_step.outcome_name == "success"
    assert final_step.is_complete is True


@pytest.mark.asyncio
async def test_llm_executor_retries_once_after_context_length_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: List[Any] = []
    overflow_error = connect.ContextLengthError(
        connect.ErrorInfo(
            code="context_length_exceeded",
            message="Context window exceeded",
            provider="openai",
            api_family="openai-responses",
            status_code=400,
            retryable=False,
        )
    )
    summary_response = _assistant_response(
        "## Goal\nCompact context\n\n## Constraints & Preferences\n- Keep exact paths.\n\n## Progress\n### Done\n- Built compact summary\n### In Progress\n- Retry request\n### Blocked\n- None\n\n## Key Decisions\n- Compact on overflow\n\n## Next Steps\n- Retry\n\n## Critical Context\n- tail"
    )
    response = _assistant_response("Recovered after compaction")
    actions: List[Any] = [
        FakeStreamHandle(
            [connect.ResponseEndEvent(response=summary_response)],
            final_response=summary_response,
        ),
        overflow_error,
        FakeStreamHandle(
            [connect.ResponseEndEvent(response=summary_response)],
            final_response=summary_response,
        ),
        FakeStreamHandle(
            [
                connect.TextDeltaEvent(index=0, delta="Recovered after compaction"),
                connect.ResponseEndEvent(response=response),
            ],
            final_response=response,
        ),
    ]

    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: SequenceAsyncLLMClient(actions, requests, **kwargs),
    )

    project = StubProject()
    node = LLMNode(
        name="node-context-overflow-retry",
        type="llm",
        model="gpt-3.5-turbo",
        extra={"model_max_tokens": 8},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-context-overflow-retry",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 60)
    recent_user = state.Message(role=models.Role.USER, text="tail")
    history.upsert_message(run, old_user)
    history.upsert_message(run, recent_user)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    steps: List[state.Step] = []
    async for step in executor.run(ExecutorInput(execution=execution, run=run)):
        steps.append(step)

    assert len(requests) == 4
    assert any(
        step.type == state.StepType.CONTEXT_COMPACTION
        for step in execution.iter_steps()
    )
    final_step = steps[-1]
    assert final_step.type == state.StepType.OUTPUT_MESSAGE
    assert final_step.message is not None
    assert final_step.message.text == "Recovered after compaction"


@pytest.mark.asyncio
async def test_llm_executor_uses_node_model_for_compaction_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_requests: List[Any] = []
    stream_requests: List[Any] = []
    summary_models: List[str] = []
    summary_providers: List[Optional[str]] = []

    class CapturingSummaryThenStreamAsyncLLMClient(SummaryThenStreamAsyncLLMClient):
        async def generate(
            self,
            model: str,
            request: Any,
            provider: Optional[str] = None,
            options: Any = None,
        ) -> connect.AssistantMessage:
            summary_models.append(model)
            summary_providers.append(provider)
            return await super().generate(model, request, provider, options)

    summary_response = _assistant_response(
        "## Goal\nCompact context\n\n## Constraints & Preferences\n- Keep exact paths.\n\n## Progress\n### Done\n- Built compact summary\n### In Progress\n- Retry request\n### Blocked\n- None\n\n## Key Decisions\n- Compact on overflow\n\n## Next Steps\n- Retry\n\n## Critical Context\n- tail"
    )
    response = _assistant_response("after compaction")
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: CapturingSummaryThenStreamAsyncLLMClient(
            summary_response,
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="after compaction"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            summary_requests,
            stream_requests,
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-default-compaction-model",
        type="llm",
        model="gpt-3.5-turbo",
        extra={"model_max_tokens": 8},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-default-compaction-model",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 60)
    recent_user = state.Message(role=models.Role.USER, text="tail")
    history.upsert_message(run, old_user)
    history.upsert_message(run, recent_user)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    async for _ in executor.run(ExecutorInput(execution=execution, run=run)):
        pass

    assert summary_models == ["gpt-3.5-turbo"]
    assert summary_providers == [None]


@pytest.mark.asyncio
async def test_llm_executor_uses_compaction_model_override(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    summary_models: List[str] = []
    summary_providers: List[Optional[str]] = []

    class CapturingSummaryThenStreamAsyncLLMClient(SummaryThenStreamAsyncLLMClient):
        async def generate(
            self,
            model: str,
            request: Any,
            provider: Optional[str] = None,
            options: Any = None,
        ) -> connect.AssistantMessage:
            summary_models.append(model)
            summary_providers.append(provider)
            return await super().generate(model, request, provider, options)

    summary_response = _assistant_response(
        "## Goal\nCompact context\n\n## Constraints & Preferences\n- Keep exact paths.\n\n## Progress\n### Done\n- Built compact summary\n### In Progress\n- Retry request\n### Blocked\n- None\n\n## Key Decisions\n- Compact on overflow\n\n## Next Steps\n- Retry\n\n## Critical Context\n- tail"
    )
    response = _assistant_response("after compaction")
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: CapturingSummaryThenStreamAsyncLLMClient(
            summary_response,
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="after compaction"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            [],
            [],
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-override-compaction-model",
        type="llm",
        model="gpt-3.5-turbo",
        extra={"model_max_tokens": 8},
        compaction=CompactionSettings(
            summary_model="openai/gpt-4.1-mini",
            summary_provider="openai",
        ),
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-override-compaction-model",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 60)
    recent_user = state.Message(role=models.Role.USER, text="tail")
    history.upsert_message(run, old_user)
    history.upsert_message(run, recent_user)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    with caplog.at_level(logging.INFO, logger="vocode"):
        async for _ in executor.run(ExecutorInput(execution=execution, run=run)):
            pass

    assert summary_models == ["openai/gpt-4.1-mini"]
    assert summary_providers == ["openai"]
    start_logs = [
        record
        for record in caplog.records
        if "Context compaction started" in record.getMessage()
    ]
    assert len(start_logs) == 1
    assert "summary_model" in start_logs[0].getMessage()
    assert "openai/gpt-4.1-mini" in start_logs[0].getMessage()
    assert "trigger_threshold_tokens" in start_logs[0].getMessage()


@pytest.mark.asyncio
async def test_llm_executor_rejects_on_permanent_compaction_summary_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    error = connect.PermanentProviderError(
        connect.ErrorInfo(
            code="provider_error",
            message="Provider request failed",
            provider="chatgpt",
            api_family="chatgpt-responses",
            status_code=400,
            retryable=False,
            raw={"error": {"message": "bad request"}},
        )
    )

    class FailingGenerateAsyncLLMClient(FakeAsyncLLMClient):
        async def generate(
            self,
            model: str,
            request: Any,
            provider: Optional[str] = None,
            options: Any = None,
        ) -> connect.AssistantMessage:
            raise error

    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: FailingGenerateAsyncLLMClient(
            _stream_with_text("unused"),
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-compaction-permanent-failure",
        type="llm",
        model="gpt-3.5-turbo",
        extra={"model_max_tokens": 8},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-compaction-permanent-failure",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 60)
    recent_user = state.Message(role=models.Role.USER, text="tail")
    history.upsert_message(run, old_user)
    history.upsert_message(run, recent_user)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    with caplog.at_level(logging.INFO, logger="vocode"):
        steps: List[state.Step] = []
        async for step in executor.run(ExecutorInput(execution=execution, run=run)):
            steps.append(step)

    assert len(steps) == 1
    assert steps[0].type == state.StepType.REJECTION
    assert steps[0].message is not None
    assert "Compaction summary generation failed." in steps[0].message.text
    assert "summary_model=gpt-3.5-turbo" in steps[0].message.text
    assert "status_code=400" in steps[0].message.text
    assert not any(
        step.type == state.StepType.CONTEXT_COMPACTION
        for step in execution.iter_steps()
    )
    failure_logs = [
        record
        for record in caplog.records
        if "Context compaction finished" in record.getMessage()
    ]
    assert len(failure_logs) == 1
    failure_message = failure_logs[0].getMessage()
    assert "status" in failure_message
    assert "failed" in failure_message
    assert "status_code" in failure_message
    assert "400" in failure_message


@pytest.mark.asyncio
async def test_llm_executor_compaction_summary_request_matches_executor_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_requests: List[Any] = []
    summary_response = _assistant_response(
        "## Goal\nCompact context\n\n## Constraints & Preferences\n- Keep exact paths.\n\n## Progress\n### Done\n- Built compact summary\n### In Progress\n- Retry request\n### Blocked\n- None\n\n## Key Decisions\n- Compact on overflow\n\n## Next Steps\n- Retry\n\n## Critical Context\n- tail"
    )
    response = _assistant_response("after compaction")

    class CapturingSummaryThenStreamAsyncLLMClient(SummaryThenStreamAsyncLLMClient):
        async def generate(
            self,
            model: str,
            request: Any,
            provider: Optional[str] = None,
            options: Any = None,
        ) -> connect.AssistantMessage:
            summary_requests.append(request)
            return await super().generate(model, request, provider, options)

    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: CapturingSummaryThenStreamAsyncLLMClient(
            summary_response,
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="after compaction"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            [],
            [],
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-compaction-request-shape",
        type="llm",
        model="gpt-3.5-turbo",
        extra={"model_max_tokens": 8},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-compaction-request-shape",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 60)
    recent_user = state.Message(role=models.Role.USER, text="tail")
    history.upsert_message(run, old_user)
    history.upsert_message(run, recent_user)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    async for _ in executor.run(ExecutorInput(execution=execution, run=run)):
        pass

    assert len(summary_requests) == 1
    assert summary_requests[0].max_output_tokens is None
    assert summary_requests[0].temperature is None
    assert summary_requests[0].reasoning is None
    assert summary_requests[0].tools == []


@pytest.mark.asyncio
async def test_llm_executor_compaction_summary_inherits_temperature_and_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_requests: List[Any] = []
    summary_response = _assistant_response(
        "## Goal\nCompact context\n\n## Constraints & Preferences\n- Keep exact paths.\n\n## Progress\n### Done\n- Built compact summary\n### In Progress\n- Retry request\n### Blocked\n- None\n\n## Key Decisions\n- Compact on overflow\n\n## Next Steps\n- Retry\n\n## Critical Context\n- tail"
    )
    response = _assistant_response("after compaction")

    class CapturingSummaryThenStreamAsyncLLMClient(SummaryThenStreamAsyncLLMClient):
        async def generate(
            self,
            model: str,
            request: Any,
            provider: Optional[str] = None,
            options: Any = None,
        ) -> connect.AssistantMessage:
            summary_requests.append(request)
            return await super().generate(model, request, provider, options)

    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: CapturingSummaryThenStreamAsyncLLMClient(
            summary_response,
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="after compaction"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            [],
            [],
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-compaction-inherit-params",
        type="llm",
        model="gpt-3.5-turbo",
        temperature=0.3,
        reasoning_effort="low",
        extra={"model_max_tokens": 8},
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-compaction-inherit-params",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 60)
    recent_user = state.Message(role=models.Role.USER, text="tail")
    history.upsert_message(run, old_user)
    history.upsert_message(run, recent_user)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    async for _ in executor.run(ExecutorInput(execution=execution, run=run)):
        pass

    assert len(summary_requests) == 1
    assert summary_requests[0].temperature == 0.3
    assert summary_requests[0].reasoning is not None
    assert summary_requests[0].reasoning.effort == "low"


@pytest.mark.asyncio
async def test_llm_executor_compaction_summary_overrides_temperature_and_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary_requests: List[Any] = []
    summary_response = _assistant_response(
        "## Goal\nCompact context\n\n## Constraints & Preferences\n- Keep exact paths.\n\n## Progress\n### Done\n- Built compact summary\n### In Progress\n- Retry request\n### Blocked\n- None\n\n## Key Decisions\n- Compact on overflow\n\n## Next Steps\n- Retry\n\n## Critical Context\n- tail"
    )
    response = _assistant_response("after compaction")

    class CapturingSummaryThenStreamAsyncLLMClient(SummaryThenStreamAsyncLLMClient):
        async def generate(
            self,
            model: str,
            request: Any,
            provider: Optional[str] = None,
            options: Any = None,
        ) -> connect.AssistantMessage:
            summary_requests.append(request)
            return await super().generate(model, request, provider, options)

    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: CapturingSummaryThenStreamAsyncLLMClient(
            summary_response,
            FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="after compaction"),
                    connect.ResponseEndEvent(response=response),
                ],
                final_response=response,
            ),
            [],
            [],
            **kwargs,
        ),
    )

    project = StubProject()
    node = LLMNode(
        name="node-compaction-override-params",
        type="llm",
        model="gpt-3.5-turbo",
        temperature=0.3,
        reasoning_effort="low",
        extra={"model_max_tokens": 8},
        compaction=CompactionSettings(
            summary_temperature=0.8,
            summary_reasoning_effort="high",
        ),
    )
    executor = LLMExecutor(config=node, project=project)

    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="node-compaction-override-params",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="x" * 60)
    recent_user = state.Message(role=models.Role.USER, text="tail")
    history.upsert_message(run, old_user)
    history.upsert_message(run, recent_user)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    async for _ in executor.run(ExecutorInput(execution=execution, run=run)):
        pass

    assert len(summary_requests) == 1
    assert summary_requests[0].temperature == 0.8
    assert summary_requests[0].reasoning is not None
    assert summary_requests[0].reasoning.effort == "high"
