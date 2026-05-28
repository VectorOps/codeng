from __future__ import annotations

import connect
import pytest

from vocode import models
from vocode import state
from vocode import settings as vocode_settings
from vocode.history.manager import HistoryManager
from vocode.runner.executors.llm.compaction import estimation as estimation_mod
from vocode.runner.executors.llm.compaction import service as service_mod
from vocode.runner.executors.llm.compaction.models import CompactionPreparationResult
from vocode.runner.executors.llm.compaction.models import CompactionSettings
from tests.stub_project import StubProject


class _FakeAsyncLLMClient:
    def __init__(self, response: connect.GenerateResponse, **kwargs) -> None:
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def generate(
        self,
        model: str,
        request: object,
        provider: str | None = None,
        options: object = None,
    ) -> connect.GenerateResponse:
        return self._response


def test_estimate_context_tokens_uses_current_messages_only() -> None:
    assistant_message = state.Message(
        role=models.Role.ASSISTANT,
        text="short assistant reply",
    )
    user_message = state.Message(
        role=models.Role.USER,
        text="short user follow up",
    )
    assistant_step = state.Step(
        execution_id=state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ).id,
        type=state.StepType.OUTPUT_MESSAGE,
        llm_usage=state.LLMUsageStats(
            prompt_tokens=500000,
            completion_tokens=4000,
        ),
    )

    prompt_messages = [
        (assistant_message, assistant_step),
        (user_message, None),
    ]

    assert estimation_mod.estimate_context_tokens(prompt_messages) == (
        estimation_mod.estimate_message_tokens(assistant_message)
        + estimation_mod.estimate_message_tokens(user_message)
    )


def test_collect_prompt_messages_drops_compacted_input_messages() -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")

    previous_execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.FINISHED,
        ),
    )
    old_input = state.Message(role=models.Role.USER, text="old input")
    history.upsert_message(run, old_input)
    previous_execution.input_message_ids.append(old_input.id)
    old_output = state.Message(role=models.Role.ASSISTANT, text="old output")
    history.upsert_message(run, old_output)
    old_output_step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=previous_execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=old_output.id,
            is_complete=True,
        ),
    )

    summary_execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            previous_id=previous_execution.id,
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )
    summary_message = state.Message(role=models.Role.ASSISTANT, text="summary")
    history.upsert_message(run, summary_message)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=summary_execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=summary_message.id,
            state=service_mod.CompactionSummaryState(
                prompt_tokens_after=10,
                trigger_threshold_ratio=0.5,
            ),
            is_complete=True,
        ),
    )

    recent_input = state.Message(role=models.Role.USER, text="recent input")
    history.upsert_message(run, recent_input)
    current_execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            previous_id=summary_execution.id,
            input_message_ids=[recent_input.id],
            status=state.RunStatus.RUNNING,
        ),
    )

    prompt_messages = service_mod.collect_prompt_messages(history, current_execution)

    assert [message.text for message, _ in prompt_messages] == [
        "summary",
        "recent input",
    ]


def test_collect_prompt_messages_uses_latest_summary_boundary_only() -> None:
    history = HistoryManager()
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

    first_summary = state.Message(role=models.Role.ASSISTANT, text="first summary")
    middle_user = state.Message(role=models.Role.USER, text="middle user")
    second_summary = state.Message(role=models.Role.ASSISTANT, text="second summary")
    final_user = state.Message(role=models.Role.USER, text="final user")
    for message in [first_summary, middle_user, second_summary, final_user]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=first_summary.id,
            state=service_mod.CompactionSummaryState(
                prompt_tokens_after=60,
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
            state=service_mod.CompactionSummaryState(
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

    prompt_messages = service_mod.collect_prompt_messages(history, execution)

    assert [message.text for message, _ in prompt_messages] == [
        "second summary",
        "final user",
    ]


def test_collect_prompt_messages_excludes_auxiliary_tool_request_steps() -> None:
    history = HistoryManager()
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

    tool_request = state.ToolCallReq(
        id="call-1",
        name="exec",
        arguments={"cmd": "echo hi"},
    )
    tool_response = state.ToolCallResp(
        id="call-1",
        name="exec",
        result={"stdout": "hi"},
    )
    output_message = state.Message(
        role=models.Role.ASSISTANT,
        text="assistant reply",
        tool_call_requests=[tool_request],
        tool_call_responses=[tool_response],
    )
    tool_request_message = state.Message(
        role=models.Role.ASSISTANT,
        text="",
        tool_call_requests=[tool_request],
        tool_call_responses=[tool_response],
    )
    user_message = state.Message(role=models.Role.USER, text="user follow up")
    for message in [output_message, tool_request_message, user_message]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=output_message.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.TOOL_REQUEST,
            message_id=tool_request_message.id,
            is_complete=True,
        ),
    )
    execution.input_message_ids.append(user_message.id)

    prompt_messages = service_mod.collect_prompt_messages(history, execution)

    assert [
        (message.text, step.type if step is not None else None)
        for message, step in prompt_messages
    ] == [
        ("user follow up", None),
        ("assistant reply", state.StepType.OUTPUT_MESSAGE),
    ]


def test_collect_prompt_messages_preserves_tail_after_inserted_latest_compaction() -> (
    None
):
    history = HistoryManager()
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

    msg1 = state.Message(role=models.Role.USER, text="msg-1")
    msg2 = state.Message(role=models.Role.ASSISTANT, text="msg-2")
    summary1 = state.Message(role=models.Role.ASSISTANT, text="summary-1")
    summary2 = state.Message(role=models.Role.ASSISTANT, text="summary-2")
    msg3 = state.Message(role=models.Role.USER, text="msg-3")
    msg4 = state.Message(role=models.Role.ASSISTANT, text="msg-4")
    summary3 = state.Message(role=models.Role.ASSISTANT, text="summary-3")
    for message in [msg1, msg2, summary1, summary2, msg3, msg4, summary3]:
        history.upsert_message(run, message)

    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=msg1.id,
            is_complete=True,
        ),
    )
    step2 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=msg2.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=summary1.id,
            state=service_mod.CompactionSummaryState(
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
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=summary2.id,
            state=service_mod.CompactionSummaryState(
                prompt_tokens_after=25,
                trigger_threshold_ratio=0.5,
            ),
            is_complete=True,
        ),
    )
    step5 = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.INPUT_MESSAGE,
            message_id=msg3.id,
            is_complete=True,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=msg4.id,
            is_complete=True,
        ),
    )
    history.insert_step(
        run,
        state.Step(
            execution_id=execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=summary3.id,
            state=service_mod.CompactionSummaryState(
                prompt_tokens_after=10,
                trigger_threshold_ratio=0.5,
            ),
            is_complete=True,
        ),
        parent_step_id=step5.parent_step_id,
        child_step_id=step5.id,
    )

    prompt_messages = service_mod.collect_prompt_messages(history, execution)

    assert [message.text for message, _ in prompt_messages] == [
        "summary-3",
        "msg-3",
        "msg-4",
    ]


def test_select_compaction_cut_index_uses_llm_usage_deltas_for_tail_budget() -> None:
    prompt_messages = [
        (
            state.Message(role=models.Role.USER, text="older user"),
            None,
        ),
        (
            state.Message(
                role=models.Role.ASSISTANT,
                text="older assistant",
                llm_usage=state.LLMUsageStats(
                    prompt_tokens=1000,
                    completion_tokens=50,
                ),
            ),
            state.Step(
                execution_id=state.NodeExecution(
                    node="llm-node",
                    input_message_ids=[],
                    status=state.RunStatus.RUNNING,
                ).id,
                type=state.StepType.OUTPUT_MESSAGE,
                is_complete=True,
            ),
        ),
        (
            state.Message(role=models.Role.USER, text="recent user"),
            None,
        ),
        (
            state.Message(
                role=models.Role.ASSISTANT,
                text="recent assistant",
                llm_usage=state.LLMUsageStats(
                    prompt_tokens=3200,
                    completion_tokens=60,
                ),
            ),
            state.Step(
                execution_id=state.NodeExecution(
                    node="llm-node",
                    input_message_ids=[],
                    status=state.RunStatus.RUNNING,
                ).id,
                type=state.StepType.OUTPUT_MESSAGE,
                is_complete=True,
            ),
        ),
        (
            state.Message(role=models.Role.USER, text="latest user"),
            None,
        ),
    ]

    cut_index = service_mod.select_compaction_cut_index(
        prompt_messages,
        CompactionSettings(keep_recent_ratio=0.2),
        input_token_limit=10000,
    )

    assert cut_index == 2


def test_select_compaction_cut_index_skips_trailing_messages_without_usage() -> None:
    first_user = state.Message(role=models.Role.USER, text="a" * 40)
    assistant = state.Message(
        role=models.Role.ASSISTANT,
        text="assistant reply",
        llm_usage=state.LLMUsageStats(
            prompt_tokens=3200,
            completion_tokens=40,
        ),
    )
    trailing_user = state.Message(role=models.Role.USER, text="b" * 200)
    prompt_messages = [
        (
            first_user,
            state.Step(
                execution_id=state.NodeExecution(
                    node="llm-node",
                    input_message_ids=[],
                    status=state.RunStatus.RUNNING,
                ).id,
                type=state.StepType.INPUT_MESSAGE,
                is_complete=True,
            ),
        ),
        (
            assistant,
            state.Step(
                execution_id=state.NodeExecution(
                    node="llm-node",
                    input_message_ids=[],
                    status=state.RunStatus.RUNNING,
                ).id,
                type=state.StepType.OUTPUT_MESSAGE,
                is_complete=True,
            ),
        ),
        (
            trailing_user,
            None,
        ),
    ]

    cut_index = service_mod.select_compaction_cut_index(
        prompt_messages,
        CompactionSettings(keep_recent_ratio=0.005),
        input_token_limit=10000,
    )

    assert cut_index == 2


def test_select_compaction_cut_index_targets_final_cutpoint() -> None:
    prompt_messages = [
        (
            state.Message(role=models.Role.USER, text="user-1"),
            None,
        ),
        (
            state.Message(
                role=models.Role.ASSISTANT,
                text="assistant-1",
                llm_usage=state.LLMUsageStats(
                    prompt_tokens=1000,
                    completion_tokens=40,
                ),
            ),
            state.Step(
                execution_id=state.NodeExecution(
                    node="llm-node",
                    input_message_ids=[],
                    status=state.RunStatus.RUNNING,
                ).id,
                type=state.StepType.OUTPUT_MESSAGE,
                is_complete=True,
            ),
        ),
        (
            state.Message(role=models.Role.USER, text="user-2"),
            None,
        ),
        (
            state.Message(
                role=models.Role.ASSISTANT,
                text="assistant-2",
                llm_usage=state.LLMUsageStats(
                    prompt_tokens=2200,
                    completion_tokens=40,
                ),
            ),
            state.Step(
                execution_id=state.NodeExecution(
                    node="llm-node",
                    input_message_ids=[],
                    status=state.RunStatus.RUNNING,
                ).id,
                type=state.StepType.OUTPUT_MESSAGE,
                is_complete=True,
            ),
        ),
        (
            state.Message(role=models.Role.USER, text="user-3"),
            None,
        ),
        (
            state.Message(
                role=models.Role.ASSISTANT,
                text="assistant-3",
                llm_usage=state.LLMUsageStats(
                    prompt_tokens=3100,
                    completion_tokens=40,
                ),
            ),
            state.Step(
                execution_id=state.NodeExecution(
                    node="llm-node",
                    input_message_ids=[],
                    status=state.RunStatus.RUNNING,
                ).id,
                type=state.StepType.OUTPUT_MESSAGE,
                is_complete=True,
            ),
        ),
        (
            state.Message(role=models.Role.USER, text="user-4"),
            None,
        ),
        (
            state.Message(
                role=models.Role.ASSISTANT,
                text="assistant-4",
                llm_usage=state.LLMUsageStats(
                    prompt_tokens=4200,
                    completion_tokens=40,
                ),
            ),
            state.Step(
                execution_id=state.NodeExecution(
                    node="llm-node",
                    input_message_ids=[],
                    status=state.RunStatus.RUNNING,
                ).id,
                type=state.StepType.OUTPUT_MESSAGE,
                is_complete=True,
            ),
        ),
    ]

    cut_index = service_mod.select_compaction_cut_index(
        prompt_messages,
        CompactionSettings(keep_recent_ratio=0.1),
        input_token_limit=10000,
    )

    assert cut_index == 6


def test_select_compaction_cut_index_uses_step_usage_when_message_usage_missing() -> (
    None
):
    assistant_step = state.Step(
        execution_id=state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ).id,
        type=state.StepType.OUTPUT_MESSAGE,
        llm_usage=state.LLMUsageStats(
            prompt_tokens=1200,
            completion_tokens=40,
        ),
        is_complete=True,
    )
    recent_step = state.Step(
        execution_id=state.NodeExecution(
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ).id,
        type=state.StepType.OUTPUT_MESSAGE,
        llm_usage=state.LLMUsageStats(
            prompt_tokens=2800,
            completion_tokens=40,
        ),
        is_complete=True,
    )
    prompt_messages = [
        (state.Message(role=models.Role.USER, text="older user"), None),
        (
            state.Message(role=models.Role.ASSISTANT, text="older assistant"),
            assistant_step,
        ),
        (state.Message(role=models.Role.USER, text="recent user"), None),
        (
            state.Message(role=models.Role.ASSISTANT, text="recent assistant"),
            recent_step,
        ),
    ]

    cut_index = service_mod.select_compaction_cut_index(
        prompt_messages,
        CompactionSettings(keep_recent_ratio=0.1),
        input_token_limit=10000,
    )

    assert cut_index == 2


def test_split_summary_inputs_excludes_tool_request_steps_and_thinking_content() -> (
    None
):
    tool_request = state.ToolCallReq(
        id="call-1",
        name="exec",
        arguments={"cmd": "echo hi"},
    )
    tool_response = state.ToolCallResp(
        id="call-1",
        name="exec",
        result={"stdout": "hi"},
    )
    output_message = state.Message(
        role=models.Role.ASSISTANT,
        text="assistant reply",
        thinking_content="hidden reasoning",
        tool_call_requests=[tool_request],
        tool_call_responses=[tool_response],
    )
    tool_request_message = state.Message(
        role=models.Role.ASSISTANT,
        text="",
        tool_call_requests=[tool_request],
        tool_call_responses=[tool_response],
    )
    summarized_messages = [
        (
            output_message,
            state.Step(
                execution_id=state.NodeExecution(
                    node="llm-node",
                    input_message_ids=[],
                    status=state.RunStatus.RUNNING,
                ).id,
                type=state.StepType.OUTPUT_MESSAGE,
                is_complete=True,
            ),
        ),
        (
            tool_request_message,
            state.Step(
                execution_id=state.NodeExecution(
                    node="llm-node",
                    input_message_ids=[],
                    status=state.RunStatus.RUNNING,
                ).id,
                type=state.StepType.TOOL_REQUEST,
                is_complete=True,
            ),
        ),
    ]

    previous_summary, transcript = service_mod._split_summary_inputs(
        summarized_messages
    )

    assert previous_summary is None
    assert "hidden reasoning" not in transcript
    assert transcript.count("[Assistant tool calls]") == 1
    assert transcript.count("[Tool results]") == 1


def test_build_summary_message_text_preserves_critical_credentials_in_fallback() -> (
    None
):
    summarized_messages = [
        (
            state.Message(
                role=models.Role.USER,
                text=(
                    "Use API key sk-test-123456 and set "
                    "OPENAI_API_KEY=sk-test-123456 before continuing"
                ),
            ),
            None,
        ),
        (
            state.Message(
                role=models.Role.ASSISTANT,
                text="I will continue with that exact key and env var.",
            ),
            state.Step(
                execution_id=state.NodeExecution(
                    node="llm-node",
                    input_message_ids=[],
                    status=state.RunStatus.RUNNING,
                ).id,
                type=state.StepType.OUTPUT_MESSAGE,
                is_complete=True,
            ),
        ),
    ]

    summary_text = service_mod.build_summary_message_text(
        summarized_messages,
        CompactionSettings(),
    )

    assert "sk-test-123456" in summary_text
    assert "OPENAI_API_KEY=sk-test-123456" in summary_text


@pytest.mark.asyncio
async def test_maybe_compact_execution_history_resummarizes_only_latest_summary_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    previous_execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.FINISHED,
        ),
    )

    old_user = state.Message(role=models.Role.USER, text="old user")
    old_assistant = state.Message(role=models.Role.ASSISTANT, text="old assistant")
    history.upsert_message(run, old_user)
    history.upsert_message(run, old_assistant)
    previous_execution.input_message_ids.append(old_user.id)
    old_output_step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=previous_execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=old_assistant.id,
            llm_usage=state.LLMUsageStats(
                prompt_tokens=1000,
                completion_tokens=40,
            ),
            is_complete=True,
        ),
    )

    summary_execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            previous_id=previous_execution.id,
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )

    summary_message = state.Message(
        role=models.Role.ASSISTANT,
        text="The conversation history before this point was compacted into the following summary:\n\n<summary>\nold summary\n</summary>",
    )
    history.upsert_message(run, summary_message)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=summary_execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=summary_message.id,
            state=service_mod.CompactionSummaryState(
                prompt_tokens_after=100,
                trigger_threshold_ratio=0.5,
            ),
            is_complete=True,
        ),
    )

    recent_user = state.Message(role=models.Role.USER, text="recent user")
    recent_assistant = state.Message(
        role=models.Role.ASSISTANT,
        text="recent assistant",
        llm_usage=state.LLMUsageStats(
            prompt_tokens=1500,
            completion_tokens=40,
        ),
    )
    history.upsert_message(run, recent_user)
    history.upsert_message(run, recent_assistant)
    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            previous_id=summary_execution.id,
            input_message_ids=[recent_user.id],
            status=state.RunStatus.RUNNING,
        ),
    )
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=recent_assistant.id,
            llm_usage=recent_assistant.llm_usage,
            is_complete=True,
        ),
    )

    captured_texts: list[str] = []

    async def _fake_generate_summary_message_text(
        credential_manager,
        summarized_messages,
        settings,
        current_model,
        current_temperature,
        current_reasoning_effort,
        capture_debug_payload,
        provider_options,
    ):
        captured_texts.extend([message.text for message, _ in summarized_messages])
        return (
            "summary",
            state.LLMUsageStats(
                prompt_tokens=111,
                completion_tokens=50,
            ),
            None,
        )

    monkeypatch.setattr(
        service_mod,
        "select_compaction_cut_index",
        lambda *args, **kwargs: 2,
    )
    monkeypatch.setattr(
        service_mod,
        "generate_summary_message_text",
        _fake_generate_summary_message_text,
    )

    preparation = CompactionPreparationResult(
        estimated_context_tokens=1500,
        input_token_limit=2000,
        should_compact=True,
        settings=CompactionSettings(trigger_threshold_ratio=0.5, keep_recent_ratio=0.1),
        current_model="chatgpt/gpt-5.4",
    )

    compaction_step = await service_mod.maybe_compact_execution_history(
        history,
        StubProject().credentials,
        execution,
        preparation,
    )

    assert compaction_step is not None
    assert captured_texts == [summary_message.text, "recent user"]


@pytest.mark.asyncio
async def test_maybe_compact_execution_history_records_summary_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = HistoryManager()
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

    first_user = state.Message(role=models.Role.USER, text="first user")
    first_assistant = state.Message(role=models.Role.ASSISTANT, text="first assistant")
    recent_user = state.Message(role=models.Role.USER, text="recent user")
    for message in [first_user, first_assistant, recent_user]:
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
            message_id=first_assistant.id,
            llm_usage=state.LLMUsageStats(
                prompt_tokens=1000,
                completion_tokens=100,
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

    response = connect.AssistantMessage(
        provider="chatgpt",
        model="chatgpt/gpt-5.4",
        api_family="responses",
        content=[connect.TextBlock(text="condensed summary")],
        finish_reason="stop",
        usage=connect.Usage(
            input_tokens=321,
            output_tokens=45,
            total_tokens=366,
            completeness="final",
        ),
    )
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: _FakeAsyncLLMClient(response, **kwargs),
    )

    preparation = CompactionPreparationResult(
        estimated_context_tokens=1000,
        input_token_limit=2000,
        should_compact=True,
        settings=CompactionSettings(trigger_threshold_ratio=0.5, keep_recent_ratio=0.1),
        current_model="chatgpt/gpt-5.4",
    )

    compaction_step = await service_mod.maybe_compact_execution_history(
        history,
        StubProject().credentials,
        execution,
        preparation,
    )

    assert compaction_step is not None
    assert compaction_step.type == state.StepType.CONTEXT_COMPACTION
    assert compaction_step.llm_usage is not None
    assert compaction_step.llm_usage.prompt_tokens == 321
    assert compaction_step.llm_usage.cached_tokens == 0
    assert compaction_step.llm_usage.completion_tokens == 45
    assert compaction_step.message is not None
    summary_state = service_mod.CompactionSummaryState.model_validate(
        compaction_step.state.model_dump(mode="python")
    )
    assert summary_state.prompt_tokens_after == 45
    assert summary_state.summary_input_tokens == 321
    assert summary_state.summary_output_tokens == 45
    assert isinstance(execution.state, service_mod.LLMExecutionState)
    assert execution.state.compaction is not None
    assert execution.state.compaction.latest_compaction_step_id == compaction_step.id
    assert execution.state.compaction.compaction_count == 1


@pytest.mark.asyncio
async def test_maybe_compact_execution_history_adjusts_retained_tail_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = HistoryManager()
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

    old_user = state.Message(role=models.Role.USER, text="old user context")
    old_assistant = state.Message(
        role=models.Role.ASSISTANT,
        text="old assistant context",
        llm_usage=state.LLMUsageStats(
            prompt_tokens=600,
            completion_tokens=40,
        ),
    )
    retained_assistant = state.Message(
        role=models.Role.ASSISTANT,
        text="retained assistant response",
        llm_usage=state.LLMUsageStats(
            prompt_tokens=900,
            completion_tokens=30,
        ),
    )
    recent_user = state.Message(role=models.Role.USER, text="recent user follow up")
    for message in [old_user, old_assistant, retained_assistant, recent_user]:
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
            llm_usage=old_assistant.llm_usage,
            is_complete=True,
        ),
    )
    retained_step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=retained_assistant.id,
            llm_usage=retained_assistant.llm_usage,
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

    async def _fake_generate_summary_message_text(*args, **kwargs):
        return (
            "summary",
            state.LLMUsageStats(
                prompt_tokens=111,
                completion_tokens=50,
            ),
            None,
        )

    monkeypatch.setattr(
        service_mod,
        "select_compaction_cut_index",
        lambda *args, **kwargs: 2,
    )
    monkeypatch.setattr(
        service_mod,
        "generate_summary_message_text",
        _fake_generate_summary_message_text,
    )

    prompt_messages = service_mod.collect_prompt_messages(history, execution)
    estimated_context_tokens = estimation_mod.estimate_context_tokens(prompt_messages)
    preparation = CompactionPreparationResult(
        estimated_context_tokens=estimated_context_tokens,
        input_token_limit=2000,
        should_compact=True,
        settings=CompactionSettings(trigger_threshold_ratio=0.5, keep_recent_ratio=0.1),
        current_model="chatgpt/gpt-5.4",
    )

    compaction_step = await service_mod.maybe_compact_execution_history(
        history,
        StubProject().credentials,
        execution,
        preparation,
    )

    assert compaction_step is not None
    expected_keep_recent_budget = int(2000 * 0.1)
    expected_delta = 900 - (expected_keep_recent_budget + 50)
    assert retained_assistant.llm_usage is not None
    assert retained_assistant.orig_llm_usage is not None
    assert retained_assistant.orig_llm_usage.prompt_tokens == 900
    assert retained_assistant.llm_usage.prompt_tokens == 900 - expected_delta
    assert retained_assistant.llm_usage.completion_tokens == 30
    assert retained_step.llm_usage is not None
    assert retained_step.llm_usage.prompt_tokens == 900 - expected_delta
    assert retained_step.llm_usage.completion_tokens == 30


@pytest.mark.asyncio
async def test_generate_summary_message_text_captures_debug_payload_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = connect.AssistantMessage(
        provider="openai",
        model="gpt-5.4",
        api_family="openai-responses",
        content=[connect.TextBlock(text="condensed summary")],
        finish_reason="stop",
        usage=connect.Usage(
            input_tokens=123,
            output_tokens=17,
            total_tokens=140,
            completeness="final",
        ),
        response_id="resp_debug",
        request_id="req_debug",
    )
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: _FakeAsyncLLMClient(response, **kwargs),
    )

    project = StubProject(
        settings=vocode_settings.Settings(
            debugging=vocode_settings.DebuggingSettings(capture_llm_payload=True)
        )
    )
    summarized_messages = [
        (state.Message(role=models.Role.USER, text="hello"), None),
    ]

    summary_text, summary_usage, summary_debug = (
        await service_mod.generate_summary_message_text(
            project.credentials,
            summarized_messages,
            CompactionSettings(),
            current_model="openai/gpt-5.4",
            current_temperature=None,
            current_reasoning_effort=None,
            capture_debug_payload=True,
            provider_options={},
        )
    )

    assert summary_usage is not None
    assert summary_usage.prompt_tokens == 123
    assert summary_usage.cached_tokens == 0
    assert "<debug_llm_payload>" not in summary_text
    assert summary_debug is not None
    assert summary_debug["response"]["response_id"] == "resp_debug"
    assert (
        summary_debug["request"]["system_prompt"]
        == "You are maintaining a continuation checkpoint for a coding workflow. Produce a compact but precise summary another LLM can resume from safely. Preserve exact file paths, tool names, identifiers, node names, outcome names, error text, and any credentials or configuration values explicitly provided by the user when relevant. Do not invent progress."
    )


@pytest.mark.asyncio
async def test_maybe_compact_execution_history_persists_debug_payload_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = HistoryManager()
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

    first_user = state.Message(role=models.Role.USER, text="first user")
    first_assistant = state.Message(role=models.Role.ASSISTANT, text="first assistant")
    recent_user = state.Message(role=models.Role.USER, text="recent user")
    for message in [first_user, first_assistant, recent_user]:
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
            message_id=first_assistant.id,
            llm_usage=state.LLMUsageStats(
                prompt_tokens=1000,
                completion_tokens=100,
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

    response = connect.AssistantMessage(
        provider="chatgpt",
        model="chatgpt/gpt-5.4",
        api_family="responses",
        content=[connect.TextBlock(text="condensed summary")],
        finish_reason="stop",
        usage=connect.Usage(
            input_tokens=321,
            output_tokens=45,
            total_tokens=366,
            completeness="final",
        ),
        response_id="resp_compaction_debug",
        request_id="req_compaction_debug",
    )
    monkeypatch.setattr(
        connect,
        "AsyncLLMClient",
        lambda *args, **kwargs: _FakeAsyncLLMClient(response, **kwargs),
    )

    preparation = CompactionPreparationResult(
        estimated_context_tokens=1000,
        input_token_limit=2000,
        should_compact=True,
        settings=CompactionSettings(trigger_threshold_ratio=0.5, keep_recent_ratio=0.1),
        current_model="chatgpt/gpt-5.4",
        capture_debug_payload=True,
    )

    compaction_step = await service_mod.maybe_compact_execution_history(
        history,
        StubProject().credentials,
        execution,
        preparation,
    )

    assert compaction_step is not None
    assert compaction_step.debug is not None
    assert compaction_step.debug["response"]["response_id"] == "resp_compaction_debug"


@pytest.mark.asyncio
async def test_maybe_compact_execution_history_splices_summary_before_retained_cross_execution_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = HistoryManager()
    run = state.WorkflowExecution(workflow_name="wf")
    first_execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            input_message_ids=[],
            status=state.RunStatus.FINISHED,
        ),
    )
    old_user = state.Message(role=models.Role.USER, text="old user")
    history.upsert_message(run, old_user)
    first_execution.input_message_ids.append(old_user.id)

    summary_execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            previous_id=first_execution.id,
            input_message_ids=[],
            status=state.RunStatus.FINISHED,
        ),
    )
    old_summary_message = state.Message(role=models.Role.ASSISTANT, text="summary")
    retained_old_output = state.Message(
        role=models.Role.ASSISTANT,
        text="retained old output",
        llm_usage=state.LLMUsageStats(
            prompt_tokens=1000,
            completion_tokens=20,
        ),
    )
    history.upsert_message(run, old_summary_message)
    history.upsert_message(run, retained_old_output)
    old_summary_step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=summary_execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=old_summary_message.id,
            state=service_mod.CompactionSummaryState(
                prompt_tokens_after=10,
                trigger_threshold_ratio=0.5,
            ),
            is_complete=True,
        ),
    )
    retained_old_step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=summary_execution.id,
            type=state.StepType.OUTPUT_MESSAGE,
            message_id=retained_old_output.id,
            llm_usage=retained_old_output.llm_usage,
            is_complete=True,
        ),
    )

    execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            previous_id=summary_execution.id,
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )
    recent_user = state.Message(role=models.Role.USER, text="recent user")
    history.upsert_message(run, recent_user)
    execution.input_message_ids.append(recent_user.id)

    async def _fake_generate_summary_message_text(*args, **kwargs):
        return (
            "new summary",
            state.LLMUsageStats(
                prompt_tokens=111,
                completion_tokens=50,
            ),
            None,
        )

    monkeypatch.setattr(
        service_mod,
        "select_compaction_cut_index",
        lambda *args, **kwargs: 1,
    )
    monkeypatch.setattr(
        service_mod,
        "generate_summary_message_text",
        _fake_generate_summary_message_text,
    )

    compaction_step = await service_mod.maybe_compact_execution_history(
        history,
        StubProject().credentials,
        execution,
        CompactionPreparationResult(
            estimated_context_tokens=1000,
            input_token_limit=2000,
            should_compact=True,
            settings=CompactionSettings(
                trigger_threshold_ratio=0.5,
                keep_recent_ratio=0.1,
            ),
            current_model="chatgpt/gpt-5.4",
        ),
    )

    assert compaction_step is not None
    assert run.step_ids == [
        old_summary_step.id,
        compaction_step.id,
        retained_old_step.id,
    ]
    assert run.get_step(retained_old_step.id).parent_step_id == compaction_step.id
    assert run.get_active_branch().head_step_id == retained_old_step.id
