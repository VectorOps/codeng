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
    history.upsert_message(run, summary_message)
    compaction_step = history.upsert_step(
        run,
        state.Step(
            workflow_execution=run,
            execution_id=current_execution.id,
            type=state.StepType.CONTEXT_COMPACTION,
            message_id=summary_message.id,
            is_complete=True,
        ),
    )
    compaction_step.state = service_mod.CompactionSummaryState(
        compacted_step_ids=[old_output_step.id],
        compacted_message_ids=[old_input.id, old_output.id],
        tokens_before=100,
        tokens_after_estimate=10,
        trigger_threshold_ratio=0.5,
    )

    recent_input = state.Message(role=models.Role.USER, text="recent input")
    history.upsert_message(run, recent_input)
    current_execution.input_message_ids.append(recent_input.id)

    prompt_messages = service_mod.collect_prompt_messages(current_execution)

    assert [message.text for message, _ in prompt_messages] == [
        "summary",
        "recent input",
    ]


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
    assert compaction_step.llm_usage is not None
    assert compaction_step.llm_usage.prompt_tokens == 321
    assert compaction_step.llm_usage.completion_tokens == 45
    assert compaction_step.state is not None
    summary_state = service_mod.CompactionSummaryState.model_validate(
        compaction_step.state.model_dump(mode="python")
    )
    assert summary_state.summary_input_tokens == 321
    assert summary_state.summary_output_tokens == 45
    assert summary_state.compacted_message_ids
