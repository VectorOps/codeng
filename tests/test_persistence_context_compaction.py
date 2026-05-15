from __future__ import annotations

import gzip
import json

from vocode import models, state
from vocode.persistence import codec as persistence_codec
from vocode.runner.executors.llm.compaction.models import CompactionSummaryState
from vocode.runner.executors.llm.compaction.models import LLMExecutionCompactionState
from vocode.runner.executors.llm.compaction.models import LLMExecutionState


def test_persistence_round_trip_preserves_message_owned_compaction_state() -> None:
    summary_message = state.Message(
        role=models.Role.SYSTEM,
        text="The conversation history before this point was compacted into the following summary:\n\n<summary>\nsummary\n</summary>",
        state=CompactionSummaryState(
            compacted_step_ids=[],
            compacted_message_ids=[],
            tokens_before=120,
            tokens_after_estimate=45,
            trigger_threshold_ratio=0.5,
        ),
    )
    execution = state.NodeExecution(
        node="llm-node",
        input_message_ids=[],
        status=state.RunStatus.RUNNING,
        state=LLMExecutionState(
            compaction=LLMExecutionCompactionState(
                latest_compaction_message_id=summary_message.id,
                compaction_count=1,
                last_compaction_tokens_before=120,
            )
        ),
    )
    step = state.Step(
        execution_id=execution.id,
        type=state.StepType.CONTEXT_COMPACTION,
        message_id=summary_message.id,
        is_complete=True,
    )
    run = state.WorkflowExecution(
        workflow_name="wf",
        node_executions={execution.id: execution},
        steps_by_id={step.id: step},
        messages_by_id={summary_message.id: summary_message},
    )

    encoded = persistence_codec.dumps_gzip(run)
    payload = json.loads(gzip.decompress(encoded).decode("utf-8"))

    assert payload["node_executions"][str(execution.id)]["state"] == {
        "selected_outcome": None,
        "compaction": {
            "latest_compaction_message_id": str(summary_message.id),
            "compaction_count": 1,
            "last_compaction_tokens_before": 120,
            "last_compaction_actual_prompt_tokens_before": None,
            "last_compaction_summary_input_tokens": None,
        },
    }
    assert payload["messages_by_id"][str(summary_message.id)]["state"] == {
        "compacted_step_ids": [],
        "compacted_message_ids": [],
        "tokens_before": 120,
        "tokens_after_estimate": 45,
        "summary_input_tokens": None,
        "summary_output_tokens": None,
        "trigger_threshold_ratio": 0.5,
        "summary_version": "v1",
    }

    restored = persistence_codec.loads_gzip(encoded)
    restored_message = restored.get_message(summary_message.id)
    assert restored_message.text == summary_message.text
