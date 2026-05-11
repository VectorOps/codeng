from __future__ import annotations

from typing import Any
from typing import List, Optional

import connect

from vocode import models, state
from vocode.logger import logger
from vocode.runner import base as runner_base
from .estimation import estimate_context_tokens, estimate_message_tokens
from .models import CompactionPreparationResult
from .models import CompactionSettings
from .models import CompactionSummaryState
from .models import LLMExecutionCompactionState
from .prompting import build_summary_generation_prompt
from .prompting import extract_wrapped_summary_text
from .prompting import resolve_compaction_instructions
from .prompting import resolve_compaction_system_prompt
from .prompting import serialize_messages_to_transcript


VALID_PRIMARY_BOUNDARY_STEP_TYPES = (state.StepType.INPUT_MESSAGE,)

VALID_FALLBACK_BOUNDARY_STEP_TYPES = (
    state.StepType.OUTPUT_MESSAGE,
    state.StepType.CONTEXT_COMPACTION,
)


SUMMARY_HEADER_LINES = [
    "## Goal",
    "Continue the same workflow with preserved prior context.",
    "",
    "## Constraints & Preferences",
    "- Preserve exact file paths, tool names, identifiers, and error text when relevant.",
    "",
    "## Progress",
    "### Done",
    "### In Progress",
    "- Continue from the retained recent context after this checkpoint.",
    "### Blocked",
    "- None recorded unless noted below.",
    "",
    "## Key Decisions",
    "- Older prompt-visible history was compacted into this checkpoint.",
    "",
    "## Next Steps",
    "- Continue with the most recent retained user-visible messages.",
    "",
    "## Critical Context",
]


def collect_prompt_messages(
    execution: state.NodeExecution,
) -> List[tuple[state.Message, Optional[state.Step]]]:
    prompt_messages = list(runner_base.iter_execution_messages(execution))
    for index in range(len(prompt_messages) - 1, -1, -1):
        message, step = prompt_messages[index]
        if step is None:
            continue
        if step.type != state.StepType.CONTEXT_COMPACTION:
            continue
        if step.state is None:
            return prompt_messages[index:]
        summary_state = CompactionSummaryState.model_validate(
            step.state.model_dump(mode="python")
        )
        compacted_step_ids = set(summary_state.compacted_step_ids)
        rebuilt_messages: List[tuple[state.Message, Optional[state.Step]]] = [
            (message, step)
        ]
        for retained_message, retained_step in prompt_messages:
            if retained_step is None:
                rebuilt_messages.append((retained_message, retained_step))
                continue
            if retained_step.id == step.id:
                continue
            if retained_step.id in compacted_step_ids:
                continue
            rebuilt_messages.append((retained_message, retained_step))
        return rebuilt_messages
    return prompt_messages


def build_summary_message_text(
    summarized_messages: List[state.Message],
    settings: CompactionSettings,
) -> str:
    previous_summaries: List[str] = []
    live_messages: List[state.Message] = []
    for message in summarized_messages:
        if message.role == models.Role.SYSTEM:
            previous_summary = extract_wrapped_summary_text(message.text)
            if previous_summary:
                previous_summaries.append(previous_summary)
                continue
        live_messages.append(message)

    transcript = serialize_messages_to_transcript(live_messages)
    summary_lines = list(SUMMARY_HEADER_LINES)
    summary_lines.insert(
        8, f"- Compacted {len(summarized_messages)} earlier prompt-visible messages."
    )
    if previous_summaries:
        summary_lines.append("- Previous summary context:")
        for previous_summary in previous_summaries[-1:]:
            for line in previous_summary.splitlines()[:12]:
                summary_lines.append(f"  {line}")
    if transcript:
        summary_lines.append("- Transcript excerpt:")
        for line in transcript.splitlines()[:20]:
            summary_lines.append(f"  {line}")
    if not previous_summaries and not transcript:
        summary_lines.append("- No prior conversation context was available.")
    return build_summary_message_envelope("\n".join(summary_lines).strip(), settings)


def build_summary_message_envelope(
    summary_body: str,
    settings: CompactionSettings,
) -> str:
    prompt_system = resolve_compaction_system_prompt(settings)
    prompt_instructions = resolve_compaction_instructions(settings)
    return (
        "The conversation history before this point was compacted into the following summary:\n\n"
        f"<summary>\n{summary_body}\n</summary>\n\n"
        f"<compaction_prompt_system>\n{prompt_system}\n</compaction_prompt_system>\n\n"
        f"<compaction_prompt_instructions>\n{prompt_instructions}\n</compaction_prompt_instructions>"
    )


def _split_summary_inputs(
    summarized_messages: List[state.Message],
) -> tuple[Optional[str], str]:
    previous_summaries: List[str] = []
    live_messages: List[state.Message] = []
    for message in summarized_messages:
        if message.role == models.Role.SYSTEM:
            previous_summary = extract_wrapped_summary_text(message.text)
            if previous_summary:
                previous_summaries.append(previous_summary)
                continue
        live_messages.append(message)
    transcript = serialize_messages_to_transcript(live_messages)
    previous_summary = previous_summaries[-1] if previous_summaries else None
    return previous_summary, transcript


async def generate_summary_message_text(
    credential_manager: Any,
    summarized_messages: List[state.Message],
    settings: CompactionSettings,
    current_model: Optional[str],
    provider_options: dict[str, Any],
) -> str:
    previous_summary, transcript = _split_summary_inputs(summarized_messages)
    if current_model is None:
        return build_summary_message_text(summarized_messages, settings)
    summary_model = settings.summary_model or current_model
    summary_provider = settings.summary_provider or None
    prompt = build_summary_generation_prompt(previous_summary, transcript, settings)
    request = connect.GenerateRequest(
        messages=[connect.UserMessage(content=prompt)],
        system_prompt=resolve_compaction_system_prompt(settings),
        max_output_tokens=1024,
    )
    try:
        async with connect.AsyncLLMClient(
            credential_manager=credential_manager
        ) as client:
            response = await client.generate(
                summary_model,
                request,
                provider=summary_provider,
                options=connect.RequestOptions(
                    provider_options=dict(provider_options or {})
                ),
            )
    except Exception as exc:
        logger.warning("Compaction summary generation failed", err=exc)
        return build_summary_message_text(summarized_messages, settings)
    summary_text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()
    if not summary_text:
        return build_summary_message_text(summarized_messages, settings)
    return build_summary_message_envelope(summary_text, settings)


async def maybe_compact_execution_history(
    history: Any,
    credential_manager: Any,
    execution: state.NodeExecution,
    preparation: CompactionPreparationResult,
) -> Optional[state.Step]:
    if not preparation.should_compact:
        return None
    prompt_messages = collect_prompt_messages(execution)
    if len(prompt_messages) < 2:
        return None

    summarize_count = select_compaction_cut_index(
        prompt_messages,
        preparation.settings,
        preparation.input_token_limit,
    )
    if summarize_count <= 0 or summarize_count >= len(prompt_messages):
        return None
    summarized_pairs = prompt_messages[:summarize_count]
    summarized_messages = [message for message, _ in summarized_pairs]
    compacted_step_ids = [
        step.id
        for _, step in summarized_pairs
        if step is not None and step.id is not None
    ]
    if not compacted_step_ids:
        return None

    workflow_execution = execution._workflow_execution
    if workflow_execution is None:
        raise ValueError("NodeExecution is not attached to a workflow execution")

    summary_message = state.Message(
        role=models.Role.SYSTEM,
        text=await generate_summary_message_text(
            credential_manager,
            summarized_messages,
            preparation.settings,
            preparation.current_model,
            preparation.provider_options,
        ),
    )
    history.upsert_message(workflow_execution, summary_message)
    final_token_estimate = estimate_context_tokens(
        [(summary_message, None)] + prompt_messages[summarize_count:]
    )
    logger.info(
        "Context compaction completed",
        source_tokens=preparation.estimated_context_tokens,
        final_tokens=final_token_estimate,
    )

    compaction_count = 0
    if execution.state is not None:
        previous_state = LLMExecutionCompactionState.model_validate(
            execution.state.model_dump(mode="python")
        )
        compaction_count = previous_state.compaction_count

    compaction_step = state.Step(
        workflow_execution=workflow_execution,
        execution_id=execution.id,
        type=state.StepType.CONTEXT_COMPACTION,
        message_id=summary_message.id,
        state=CompactionSummaryState(
            compacted_step_ids=compacted_step_ids,
            tokens_before=preparation.estimated_context_tokens,
            tokens_after_estimate=final_token_estimate,
            trigger_threshold_ratio=(preparation.settings.trigger_threshold_ratio),
        ),
        is_complete=True,
    )
    persisted_step = history.upsert_step(workflow_execution, compaction_step)
    execution.state = LLMExecutionCompactionState(
        latest_compaction_step_id=persisted_step.id,
        compaction_count=compaction_count + 1,
        last_compaction_tokens_before=preparation.estimated_context_tokens,
    )
    history.upsert_node_execution(workflow_execution, execution)
    return persisted_step


def select_compaction_cut_index(
    prompt_messages: List[tuple[state.Message, Optional[state.Step]]],
    settings: CompactionSettings,
    input_token_limit: Optional[int],
) -> int:
    if len(prompt_messages) < 2:
        return 0
    if input_token_limit is None or input_token_limit <= 0:
        return max(1, len(prompt_messages) - 1)

    keep_recent_budget = max(
        1, int(float(input_token_limit) * settings.keep_recent_ratio)
    )
    accumulated_tokens = 0

    for index in range(len(prompt_messages) - 1, -1, -1):
        message, step = prompt_messages[index]
        accumulated_tokens += estimate_message_tokens(message)
        if accumulated_tokens < keep_recent_budget:
            continue
        primary_index = _find_boundary_index(
            prompt_messages,
            start_index=index,
            allowed_step_types=VALID_PRIMARY_BOUNDARY_STEP_TYPES,
        )
        if primary_index is not None:
            return _adjust_cut_index_for_tool_round(prompt_messages, primary_index)
        fallback_index = _find_boundary_index(
            prompt_messages,
            start_index=index,
            allowed_step_types=VALID_FALLBACK_BOUNDARY_STEP_TYPES,
        )
        if fallback_index is not None:
            return _adjust_cut_index_for_tool_round(prompt_messages, fallback_index)
    return max(1, len(prompt_messages) - 1)


def _find_boundary_index(
    prompt_messages: List[tuple[state.Message, Optional[state.Step]]],
    start_index: int,
    allowed_step_types: tuple[state.StepType, ...],
) -> Optional[int]:
    for index in range(start_index, 0, -1):
        _, step = prompt_messages[index]
        if step is None:
            continue
        if step.type in allowed_step_types:
            return index
    return None


def _adjust_cut_index_for_tool_round(
    prompt_messages: List[tuple[state.Message, Optional[state.Step]]],
    cut_index: int,
) -> int:
    adjusted_index = cut_index
    while adjusted_index > 0:
        message, step = prompt_messages[adjusted_index]
        if step is None:
            return adjusted_index
        if step.type != state.StepType.OUTPUT_MESSAGE:
            return adjusted_index
        if (
            message.tool_call_responses
            and not message.tool_call_requests
            and not message.text
        ):
            adjusted_index -= 1
            continue
        return adjusted_index
    return cut_index
