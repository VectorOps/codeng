import pytest
import connect

from vocode import models, state
from vocode.history.manager import HistoryManager
from vocode.runner.base import ExecutorInput
from vocode.runner.executors.llm.compaction import CompactionSettings
from vocode.runner.executors.llm.compaction import CompactionSummaryState
from vocode.runner.executors.llm.compaction import LLMExecutionCompactionState
from vocode.runner.executors.llm.compaction.models import LLMExecutionState
from vocode.runner.executors.llm.llm import (
    LLMExecutor,
    LLMStepState,
    ToolCallProviderState,
)
from vocode.runner.executors.llm.models import LLMNode
from tests.stub_project import StubProject


class _FakeStreamHandle:
    def __init__(
        self,
        events: list[connect.StreamEvent],
        final_response: connect.AssistantMessage,
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
        return self._final_response


class _FakeAsyncLLMClient:
    def __init__(self, stream_handle: _FakeStreamHandle, **kwargs) -> None:
        self._stream_handle = stream_handle

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, model: str, request: object, options: object = None):
        return self._stream_handle


def test_build_connect_messages_with_tool_call_and_tool_result() -> None:
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    tool_req = state.ToolCallReq(
        id="call-test-tool-req",
        name="test-tool",
        arguments={"x": 1},
        state=ToolCallProviderState(reasoning_signature="sig-123"),
    )
    tool_resp = state.ToolCallResp(
        id="call-1",
        name="test-tool",
        status=state.ToolCallStatus.COMPLETED,
        result={"ok": True},
    )
    assistant_msg = state.Message(
        role=models.Role.ASSISTANT,
        text="call tool",
        tool_call_requests=[tool_req],
        tool_call_responses=[tool_resp],
    )
    history.upsert_message(run, assistant_msg)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=assistant_msg.id,
            is_complete=True,
        ),
    )

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=execution, run=run)

    system_prompt, messages = executor.build_connect_messages(
        executor._iter_prompt_messages(inp)
    )

    assert system_prompt is None
    assert len(messages) == 2

    first = messages[0]
    assert isinstance(first, connect.AssistantMessage)
    assert first.content[0].type == "text"
    assert first.content[0].text == "call tool"
    assert first.content[1].type == "tool_call"
    assert first.content[1].name == "test-tool"
    assert first.content[1].annotations == {"reasoning_signature": "sig-123"}

    second = messages[1]
    assert isinstance(second, connect.ToolResultMessage)
    assert second.tool_call_id == "call-1"
    assert second.tool_name == "test-tool"
    assert second.content[0].text == '{"ok": true}'


def test_build_connect_messages_applies_preprocessors_to_system_prompt() -> None:
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
        system="base system",
        preprocessors=[
            models.PreprocessorSpec(
                name="string_inject",
                options={"text": "prefix", "separator": "\n--\n"},
                mode=models.Role.SYSTEM,
                prepend=True,
            )
        ],
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=execution, run=run)

    system_prompt, messages = executor.build_connect_messages(
        executor._iter_prompt_messages(inp)
    )

    assert system_prompt is not None
    assert system_prompt.startswith("prefix")
    assert "base system" in system_prompt
    assert "\n--\n" in system_prompt
    assert messages == []


def test_build_connect_messages_keeps_linear_history_order() -> None:
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
        system="base system",
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    user_msg = state.Message(role=models.Role.USER, text="hello")
    assistant_msg = state.Message(role=models.Role.ASSISTANT, text="hi there")
    tool_msg = state.Message(
        role=models.Role.ASSISTANT,
        text="",
        tool_call_responses=[
            state.ToolCallResp(
                id="call-1",
                name="test-tool",
                status=state.ToolCallStatus.COMPLETED,
                result={"ok": True},
            )
        ],
    )
    for msg in [user_msg, assistant_msg, tool_msg]:
        history.upsert_message(run, msg)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=user_msg.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=assistant_msg.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=tool_msg.id,
            is_complete=True,
        ),
    )

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=execution, run=run)

    system_prompt, messages = executor.build_connect_messages(
        executor._iter_prompt_messages(inp)
    )

    assert system_prompt == "base system"
    assert len(messages) == 3
    assert isinstance(messages[0], connect.UserMessage)
    assert messages[0].content == "hello"
    assert isinstance(messages[1], connect.AssistantMessage)
    assert messages[1].content[0].type == "text"
    assert messages[1].content[0].text == "hi there"
    assert isinstance(messages[2], connect.ToolResultMessage)
    assert messages[2].tool_call_id == "call-1"
    assert messages[2].tool_name == "test-tool"
    assert messages[2].content[0].text == '{"ok": true}'


def test_build_connect_messages_copies_llm_step_state_provider_fields_to_message() -> (
    None
):
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    assistant_msg = state.Message(role=models.Role.ASSISTANT, text="hello")
    history.upsert_message(run, assistant_msg)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=assistant_msg.id,
            state=LLMStepState(protocol_state={"cache_control": {"type": "ephemeral"}}),
            is_complete=True,
        ),
    )

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=execution, run=run)

    _, messages = executor.build_connect_messages(executor._iter_prompt_messages(inp))

    assert len(messages) == 1
    first = messages[0]
    assert isinstance(first, connect.AssistantMessage)
    assert first.content[0].text == "hello"
    assert first.protocol_meta == {"cache_control": {"type": "ephemeral"}}


def test_build_connect_messages_uses_active_history_view_after_user_input_edit() -> (
    None
):
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    initial_message = state.Message(role=models.Role.USER, text="initial input")
    history.upsert_message(run, initial_message)
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            input_message_ids=[initial_message.id],
            status=state.RunStatus.RUNNING,
        ),
    )

    prompt_message = state.Message(role=models.Role.ASSISTANT, text="prompt")
    old_user_message = state.Message(role=models.Role.USER, text="old user input")
    old_output_message = state.Message(role=models.Role.ASSISTANT, text="old output")
    for message in [prompt_message, old_user_message, old_output_message]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=prompt_message.id,
            is_complete=True,
        ),
    )
    old_input_step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=old_user_message.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=old_output_message.id,
            is_complete=True,
        ),
    )

    history.edit_user_input(run, old_input_step.id, "new user input")
    active_execution = run.get_last_step().execution

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=active_execution, run=run)

    _, messages = executor.build_connect_messages(executor._iter_prompt_messages(inp))

    assert len(messages) == 5
    assert isinstance(messages[0], connect.UserMessage)
    assert messages[0].content == "initial input"
    assert isinstance(messages[1], connect.AssistantMessage)
    assert messages[1].content[0].text == "prompt"
    assert isinstance(messages[2], connect.UserMessage)
    assert messages[2].content == "old user input"
    assert isinstance(messages[3], connect.AssistantMessage)
    assert messages[3].content[0].text == "old output"
    assert isinstance(messages[4], connect.UserMessage)
    assert messages[4].content == "new user input"


def test_build_connect_messages_omits_unresolved_tool_calls_after_history_edit() -> (
    None
):
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    tool_req = state.ToolCallReq(
        id="call-test-tool",
        name="test-tool",
        arguments={"x": 1},
    )
    assistant_msg = state.Message(
        role=models.Role.ASSISTANT,
        text="call tool",
        tool_call_requests=[tool_req],
    )
    rejection_msg = state.Message(role=models.Role.USER, text="wrong direction")
    history.upsert_message(run, assistant_msg)
    history.upsert_message(run, rejection_msg)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=assistant_msg.id,
            is_complete=True,
        ),
    )
    tool_step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.TOOL_REQUEST,
            message_id=assistant_msg.id,
            is_complete=True,
        ),
    )
    rejection_step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            parent_step_id=tool_step.id,
            type=state.StepType.REJECTION,
            message_id=rejection_msg.id,
            is_complete=True,
        ),
    )

    history.edit_user_input(run, rejection_step.id, "updated input")
    active_execution = run.get_last_step().execution

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=active_execution, run=run)
    _, messages = executor.build_connect_messages(executor._iter_prompt_messages(inp))

    assert len(messages) == 2
    assert isinstance(messages[0], connect.AssistantMessage)
    assert len(messages[0].content) == 1
    assert messages[0].content[0].type == "text"
    assert messages[0].content[0].text == "call tool"
    assert isinstance(messages[-1], connect.UserMessage)
    assert messages[-1].content == "updated input"


def test_build_step_from_message_reuses_existing_message_id_for_updates() -> None:
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )
    base_step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )

    executor = LLMExecutor(config=cfg, project=StubProject())

    interim_step = executor._build_step_from_message(
        base_step,
        role=models.Role.ASSISTANT,
        step_type=state.StepType.OUTPUT_MESSAGE,
        text="first",
    )
    final_step = executor._build_step_from_message(
        interim_step,
        role=models.Role.ASSISTANT,
        step_type=state.StepType.OUTPUT_MESSAGE,
        text="second",
        is_complete=True,
    )

    assert interim_step.message_id is not None
    assert final_step.message_id == interim_step.message_id
    assert final_step.message is not None
    assert final_step.message.text == "second"
    assert final_step.message.llm_usage is None
    assert len(run.messages_by_id) == 1


def test_build_step_from_message_persists_message_usage() -> None:
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )
    base_step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
        ),
    )

    executor = LLMExecutor(config=cfg, project=StubProject())
    usage = state.LLMUsageStats(
        prompt_tokens=11,
        completion_tokens=5,
        cost_dollars=0.2,
        model_name="test-model",
    )

    final_step = executor._build_step_from_message(
        base_step,
        role=models.Role.ASSISTANT,
        step_type=state.StepType.OUTPUT_MESSAGE,
        text="second",
        usage=usage,
        is_complete=True,
    )

    assert final_step.message is not None
    assert final_step.message.llm_usage is not None
    assert final_step.message.llm_usage.prompt_tokens == 11
    assert final_step.message.llm_usage.completion_tokens == 5


@pytest.mark.asyncio
async def test_run_streaming_reuses_same_message_id_across_intermediate_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=execution, run=run)

    user_msg = state.Message(role=models.Role.USER, text="Why?")
    history.upsert_message(run, user_msg)
    execution.input_message_ids.append(user_msg.id)

    final_response = connect.AssistantMessage(
        provider="openai",
        model="test-model",
        api_family="openai-responses",
        content=[connect.TextBlock(text="Why do")],
        finish_reason="stop",
        usage=connect.Usage(
            input_tokens=0, output_tokens=2, total_tokens=2, completeness="final"
        ),
    )
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: _FakeAsyncLLMClient(
            _FakeStreamHandle(
                [
                    connect.TextDeltaEvent(index=0, delta="Why"),
                    connect.TextDeltaEvent(index=0, delta=" do"),
                    connect.ResponseEndEvent(response=final_response),
                ],
                final_response=final_response,
            ),
            **kwargs,
        ),
    )

    steps = []
    async for step in executor.run(inp):
        steps.append(step)

    assert len(steps) == 4
    assert steps[0].message_id is None
    assert steps[1].message_id is not None
    assert steps[2].message_id == steps[1].message_id
    assert steps[3].message_id == steps[1].message_id
    assert len(run.messages_by_id) == 2
    message = run.get_message(steps[1].message_id)
    assert message.text == "Why do"


def test_llm_node_compaction_defaults_and_state_models_roundtrip() -> None:
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    assert cfg.compaction == CompactionSettings()
    summary_message = state.Message(
        role=models.Role.SYSTEM,
        text="summary checkpoint",
    )
    step = state.Step(
        execution_id=state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ).id,
        type=state.StepType.CONTEXT_COMPACTION,
        message_id=summary_message.id,
        state=CompactionSummaryState(
            compacted_step_ids=[],
            prompt_tokens_before=1200,
            prompt_tokens_after=400,
            trigger_threshold_ratio=0.5,
        ),
    )
    execution = state.NodeExecution(
        node="llm-node",
        input_message_ids=[],
        status=state.RunStatus.RUNNING,
        state=LLMExecutionState(
            compaction=LLMExecutionCompactionState(
                latest_compaction_step_id=step.id,
                compaction_count=1,
            )
        ),
    )
    step.execution_id = execution.id

    payload = state.WorkflowExecution(
        workflow_name="wf",
        node_executions={execution.id: execution},
        steps_by_id={step.id: step},
        messages_by_id={summary_message.id: summary_message},
    ).model_dump(mode="json")

    assert payload["node_executions"][str(execution.id)]["state"] == {
        "selected_outcome": None,
        "compaction": {
            "latest_compaction_step_id": str(step.id),
            "compaction_count": 1,
            "last_compaction_tokens_before": None,
            "last_compaction_actual_prompt_tokens_before": None,
            "last_compaction_summary_input_tokens": None,
        },
    }
    assert payload["steps_by_id"][str(step.id)]["type"] == "context_compaction"
    assert payload["steps_by_id"][str(step.id)]["state"] == {
        "compacted_step_ids": [],
        "compacted_message_ids": [],
        "prompt_tokens_before": 1200,
        "prompt_tokens_after": 400,
        "summary_input_tokens": None,
        "summary_output_tokens": None,
        "trigger_threshold_ratio": 0.5,
        "summary_version": "v1",
    }


def test_build_connect_messages_uses_latest_compaction_boundary() -> None:
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
        system="base system",
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="old user")
    old_assistant = state.Message(role=models.Role.ASSISTANT, text="old assistant")
    summary = state.Message(
        role=models.Role.SYSTEM,
        text="summary checkpoint",
    )
    recent_user = state.Message(role=models.Role.USER, text="recent user")
    recent_assistant = state.Message(
        role=models.Role.ASSISTANT, text="recent assistant"
    )
    for msg in [old_user, old_assistant, summary, recent_user, recent_assistant]:
        history.upsert_message(run, msg)

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
            message_id=old_assistant.id,
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
            state=CompactionSummaryState(
                compacted_step_ids=[],
                compacted_message_ids=[old_user.id, old_assistant.id],
                prompt_tokens_before=100,
                prompt_tokens_after=20,
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

    executor = LLMExecutor(config=cfg, project=StubProject())

    system_prompt, messages = executor.build_connect_messages(
        executor._iter_prompt_messages(ExecutorInput(execution=execution, run=run))
    )

    assert system_prompt == "base system\n\nsummary checkpoint"
    assert len(messages) == 2
    assert isinstance(messages[0], connect.UserMessage)
    assert messages[0].content == "recent user"
    assert isinstance(messages[1], connect.AssistantMessage)
    assert messages[1].content[0].text == "recent assistant"


def test_build_connect_messages_uses_latest_of_multiple_compaction_boundaries() -> None:
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    first_summary = state.Message(role=models.Role.SYSTEM, text="first summary")
    middle_user = state.Message(role=models.Role.USER, text="middle user")
    second_summary = state.Message(role=models.Role.SYSTEM, text="second summary")
    final_user = state.Message(role=models.Role.USER, text="final user")
    for msg in [first_summary, middle_user, second_summary, final_user]:
        history.upsert_message(run, msg)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=first_summary.id,
            state=CompactionSummaryState(
                compacted_step_ids=[],
                compacted_message_ids=[],
                prompt_tokens_before=100,
                prompt_tokens_after=50,
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
            message_id=middle_user.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=second_summary.id,
            state=CompactionSummaryState(
                compacted_step_ids=[],
                compacted_message_ids=[middle_user.id],
                prompt_tokens_before=50,
                prompt_tokens_after=20,
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
            message_id=final_user.id,
            is_complete=True,
        ),
    )

    executor = LLMExecutor(config=cfg, project=StubProject())

    system_prompt, messages = executor.build_connect_messages(
        executor._iter_prompt_messages(ExecutorInput(execution=execution, run=run))
    )

    assert system_prompt == "second summary"
    assert len(messages) == 1
    assert isinstance(messages[0], connect.UserMessage)
    assert messages[0].content == "final user"


def test_build_connect_messages_does_not_leak_compacted_messages_after_boundary() -> (
    None
):
    history = HistoryManager()
    cfg = LLMNode(
        name="llm-node",
        model="test-model",
        confirmation=models.Confirmation.AUTO,
        system="base system",
    )

    run = state.WorkflowExecution(workflow_name="wf")
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="old user")
    old_assistant = state.Message(role=models.Role.ASSISTANT, text="old assistant")
    summary = state.Message(
        role=models.Role.SYSTEM,
        text="summary checkpoint",
    )
    recent_user = state.Message(role=models.Role.USER, text="recent user")
    for message in [old_user, old_assistant, summary, recent_user]:
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
            message_id=old_assistant.id,
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
            state=CompactionSummaryState(
                compacted_step_ids=[],
                compacted_message_ids=[old_user.id, old_assistant.id],
                prompt_tokens_before=100,
                prompt_tokens_after=20,
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
            message_id=recent_user.id,
            is_complete=True,
        ),
    )

    executor = LLMExecutor(config=cfg, project=StubProject())
    inp = ExecutorInput(execution=execution, run=run)
    system_prompt, messages = executor.build_connect_messages(
        executor._iter_prompt_messages(inp)
    )

    assert system_prompt == "base system\n\nsummary checkpoint"
    assert len(messages) == 1
    assert isinstance(messages[0], connect.UserMessage)
    assert messages[0].content == "recent user"
