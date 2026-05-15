from __future__ import annotations

import connect
import pytest

from vocode import models
from vocode import state
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

    current_execution = history.upsert_node_execution(
        run,
        state.NodeExecution(
            workflow_execution=run,
            node="llm-node",
            previous_id=previous_execution.id,
            input_message_ids=[],
            status=state.RunStatus.RUNNING,
        ),
    )
    summary_message = state.Message(role=models.Role.SYSTEM, text="summary")
    summary_message.state = service_mod.CompactionSummaryState(
        compacted_step_ids=[old_output_step.id],
        compacted_message_ids=[old_input.id, old_output.id],
        tokens_before=100,
        tokens_after_estimate=10,
        trigger_threshold_ratio=0.5,
    )
    history.upsert_message(run, summary_message)
    history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=current_execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=summary_message.id,
            is_complete=True,
        ),
    )

    recent_input = state.Message(role=models.Role.USER, text="recent input")
    history.upsert_message(run, recent_input)
    current_execution.input_message_ids.append(recent_input.id)

    prompt_messages = service_mod.collect_prompt_messages(current_execution)

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

    first_summary = state.Message(role=models.Role.SYSTEM, text="first summary")
    first_summary.state = service_mod.CompactionSummaryState(
        compacted_step_ids=[],
        compacted_message_ids=[],
        tokens_before=100,
        tokens_after_estimate=60,
        trigger_threshold_ratio=0.5,
    )
    middle_user = state.Message(role=models.Role.USER, text="middle user")
    second_summary = state.Message(role=models.Role.SYSTEM, text="second summary")
    second_summary.state = service_mod.CompactionSummaryState(
        compacted_step_ids=[],
        compacted_message_ids=[middle_user.id],
        tokens_before=60,
        tokens_after_estimate=20,
        trigger_threshold_ratio=0.5,
    )
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

    prompt_messages = service_mod.collect_prompt_messages(execution)

    assert [message.text for message, _ in prompt_messages] == [
        "second summary",
        "final user",
    ]


def test_select_compaction_cut_index_uses_llm_usage_deltas_for_tail_budget() -> None:
    prompt_messages = [
        (
            state.Message(role=models.Role.USER, text="older user"),
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
    ]

    cut_index = service_mod.select_compaction_cut_index(
        prompt_messages,
        CompactionSettings(keep_recent_ratio=0.2),
        input_token_limit=10000,
    )

    assert cut_index == 1


def test_select_compaction_cut_index_estimates_only_trailing_messages_without_usage() -> (
    None
):
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
    ]

    cut_index = service_mod.select_compaction_cut_index(
        prompt_messages,
        CompactionSettings(keep_recent_ratio=0.005),
        input_token_limit=10000,
    )

    assert cut_index == 2


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
    assert compaction_step.llm_usage.completion_tokens == 45
    assert compaction_step.message is not None
    assert compaction_step.message.state is not None
    summary_state = service_mod.CompactionSummaryState.model_validate(
        compaction_step.message.state.model_dump(mode="python")
    )
    assert summary_state.summary_input_tokens == 321
    assert summary_state.summary_output_tokens == 45
    assert summary_state.compacted_message_ids
    assert isinstance(execution.state, service_mod.LLMExecutionState)
    assert execution.state.compaction is not None
    assert (
        execution.state.compaction.latest_compaction_message_id
        == compaction_step.message_id
    )
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
        return "summary", None

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

    prompt_messages = service_mod.collect_prompt_messages(execution)
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
    summary_message = compaction_step.message
    assert summary_message is not None
    remaining_pairs = [(retained_assistant, retained_step), (recent_user, None)]
    expected_delta = estimated_context_tokens - estimation_mod.estimate_context_tokens(
        [(summary_message, None)] + remaining_pairs
    )
    assert retained_assistant.llm_usage is not None
    assert retained_assistant.llm_usage.prompt_tokens == 900 - expected_delta
    assert retained_assistant.llm_usage.completion_tokens == 30
    assert retained_step.llm_usage is not None
    assert retained_step.llm_usage.prompt_tokens == 900 - expected_delta
    assert retained_step.llm_usage.completion_tokens == 30
