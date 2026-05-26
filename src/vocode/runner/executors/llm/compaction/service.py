from __future__ import annotations

import json
from typing import Any
from typing import List, Optional
from uuid import UUID

import connect

from vocode import models, state
from vocode.logger import logger
from .. import debug_capture as debug_capture_mod
from .estimation import estimate_context_tokens
from .estimation import get_threshold_context_tokens
from .models import CompactionPreparationResult
from .models import CompactionSettings
from .models import LLMExecutionState
from .models import CompactionSummaryState
from .models import LLMExecutionCompactionState
from .prompting import build_summary_generation_prompt
from .prompting import extract_wrapped_summary_text
from .prompting import resolve_compaction_instructions
from .prompting import resolve_compaction_system_prompt
from .prompting import serialize_messages_to_transcript


def _build_summary_usage(
    response: connect.GenerateResponse,
    model_name: str,
) -> Optional[state.LLMUsageStats]:
    usage = response.usage
    if usage is None:
        return None
    return state.LLMUsageStats(
        prompt_tokens=int(usage.input_tokens or 0) + int(usage.cache_read_tokens or 0),
        completion_tokens=int(usage.output_tokens or 0),
        cost_dollars=0.0,
        model_name=model_name,
    )


def _message_character_count(message: state.Message) -> int:
    total = len(message.text or "")
    for req in message.tool_call_requests:
        total += len(req.name)
        total += len(json.dumps(req.arguments, sort_keys=True))
    for resp in message.tool_call_responses:
        total += len(resp.name)
        if resp.result is not None:
            total += len(json.dumps(resp.result, sort_keys=True))
    return total


def _messages_character_count(messages: List[state.Message]) -> int:
    return sum(_message_character_count(message) for message in messages)


def _get_message_prompt_tokens(message: state.Message) -> Optional[int]:
    if message.llm_usage is None:
        return None
    return int(message.llm_usage.prompt_tokens)


def _get_pair_prompt_tokens(
    message: state.Message,
    step: Optional[state.Step],
) -> Optional[int]:
    message_prompt_tokens = _get_message_prompt_tokens(message)
    if message_prompt_tokens is not None:
        return message_prompt_tokens
    if step is None or step.llm_usage is None:
        return None
    return int(step.llm_usage.prompt_tokens)


def _resolve_summary_model_name(
    settings: CompactionSettings,
    current_model: Optional[str],
) -> Optional[str]:
    return settings.summary_model or current_model


def _get_summary_output_tokens(
    summary_usage: Optional[state.LLMUsageStats],
) -> Optional[int]:
    if summary_usage is None:
        return None
    return int(summary_usage.completion_tokens)


def _resolve_threshold_token_source(
    prompt_messages: List[tuple[state.Message, Optional[state.Step]]],
) -> str:
    for message, _ in reversed(prompt_messages):
        if message.llm_usage is not None:
            return "last_message_usage"
    return "estimated_context"


def _get_latest_prompt_tokens(
    prompt_messages: List[tuple[state.Message, Optional[state.Step]]],
) -> Optional[int]:
    for message, step in reversed(prompt_messages):
        prompt_tokens = _get_pair_prompt_tokens(message, step)
        if prompt_tokens is not None:
            return prompt_tokens
    return None


def _is_primary_cut_boundary(
    message: state.Message,
    step: Optional[state.Step],
) -> bool:
    if step is not None and step.type == state.StepType.INPUT_MESSAGE:
        return True
    return step is None and message.role == models.Role.USER


def _is_fallback_cut_boundary(
    message: state.Message,
    step: Optional[state.Step],
) -> bool:
    if step is None:
        return False
    return step.type in VALID_FALLBACK_BOUNDARY_STEP_TYPES


def _is_prompt_visible_summary_pair(
    message: state.Message,
    step: Optional[state.Step],
) -> bool:
    if step is None:
        return True
    return step.type in (
        state.StepType.OUTPUT_MESSAGE,
        state.StepType.INPUT_MESSAGE,
        state.StepType.CONTEXT_COMPACTION,
    )


def _build_compaction_prompt_usage_delta(
    actual_prompt_tokens_before: Optional[int],
    summary_usage: Optional[state.LLMUsageStats],
    input_token_limit: Optional[int],
    settings: CompactionSettings,
) -> int:
    if actual_prompt_tokens_before is None:
        return 0
    if summary_usage is None:
        return 0
    if input_token_limit is None or input_token_limit <= 0:
        return 0
    keep_recent_budget = max(
        1, int(float(input_token_limit) * settings.keep_recent_ratio)
    )
    target_prompt_tokens_after = keep_recent_budget + int(
        summary_usage.completion_tokens
    )
    return max(0, actual_prompt_tokens_before - target_prompt_tokens_after)


def _apply_compaction_prompt_usage_delta(
    history: Any,
    workflow_execution: state.WorkflowExecution,
    remaining_pairs: List[tuple[state.Message, Optional[state.Step]]],
    prompt_token_delta: int,
) -> None:
    if prompt_token_delta <= 0:
        return
    for message, step in remaining_pairs:
        message_usage = message.llm_usage
        if message_usage is not None:
            if message.orig_llm_usage is None:
                message.orig_llm_usage = message_usage.model_copy(deep=True)
            message_usage.prompt_tokens = max(
                0,
                int(message_usage.prompt_tokens) - prompt_token_delta,
            )
            history.upsert_message(workflow_execution, message)
        if step is None or step.llm_usage is None:
            continue
        if step.llm_usage is not message_usage:
            step.llm_usage.prompt_tokens = max(
                0,
                int(step.llm_usage.prompt_tokens) - prompt_token_delta,
            )
        history.upsert_step(workflow_execution, step)


class CompactionSummaryGenerationError(Exception):
    def __init__(
        self,
        message: str,
        *,
        cause: connect.ConnectError,
        summary_model: str,
        summary_provider: Optional[str],
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.summary_model = summary_model
        self.summary_provider = summary_provider


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


def get_compaction_summary_state(
    step: Optional[state.Step],
) -> Optional[CompactionSummaryState]:
    if step is None or step.state is None:
        return None
    return CompactionSummaryState.model_validate(step.state.model_dump(mode="python"))


def is_compaction_summary_step(step: Optional[state.Step]) -> bool:
    return get_compaction_summary_state(step) is not None


def collect_prompt_messages(
    history: Any,
    execution: state.NodeExecution,
) -> List[tuple[state.Message, Optional[state.Step]]]:
    prompt_messages = [
        (message, step)
        for message, step in history.iter_execution_message_pairs(execution)
        if _is_prompt_visible_summary_pair(message, step)
    ]
    for index in range(len(prompt_messages) - 1, -1, -1):
        _, step = prompt_messages[index]
        if step is not None and step.type == state.StepType.CONTEXT_COMPACTION:
            summary_pair = prompt_messages[index]
            tail_pairs = prompt_messages[index + 1 :]
            return [summary_pair] + tail_pairs
    return prompt_messages


def build_summary_message_text(
    summarized_messages: List[tuple[state.Message, Optional[state.Step]]],
    settings: CompactionSettings,
) -> str:
    previous_summaries: List[str] = []
    live_messages: List[state.Message] = []
    for message, step in summarized_messages:
        if step is not None and step.type == state.StepType.CONTEXT_COMPACTION:
            previous_summary = extract_wrapped_summary_text(message.text)
            if previous_summary:
                previous_summaries.append(previous_summary)
                continue
        if not _is_prompt_visible_summary_pair(message, step):
            continue
        live_messages.append(message)

    transcript = serialize_messages_to_transcript(live_messages)
    summary_lines = list(SUMMARY_HEADER_LINES)
    summary_lines.insert(
        8,
        (f"- Compacted {len(summarized_messages)} earlier prompt-visible messages."),
    )
    if previous_summaries:
        summary_lines.append("- Previous summary context:")
        for previous_summary in previous_summaries[-1:]:
            for line in previous_summary.splitlines():
                summary_lines.append(f"  {line}")
    if transcript:
        summary_lines.append("- Transcript excerpt:")
        for line in transcript.splitlines():
            summary_lines.append(f"  {line}")
    if not previous_summaries and not transcript:
        summary_lines.append("- No prior conversation context was available.")
    return build_summary_message_envelope("\n".join(summary_lines).strip(), settings)


def build_summary_message_envelope(
    summary_body: str,
    settings: CompactionSettings,
) -> str:
    return (
        "The conversation history before this point was compacted into the following summary:\n\n"
        f"<summary>\n{summary_body}\n</summary>"
    )


def _split_summary_inputs(
    summarized_messages: List[tuple[state.Message, Optional[state.Step]]],
) -> tuple[Optional[str], str]:
    previous_summaries: List[str] = []
    live_messages: List[state.Message] = []
    for message, step in summarized_messages:
        if step is not None and step.type == state.StepType.CONTEXT_COMPACTION:
            previous_summary = extract_wrapped_summary_text(message.text)
            if previous_summary:
                previous_summaries.append(previous_summary)
                continue
        if not _is_prompt_visible_summary_pair(message, step):
            continue
        live_messages.append(message)
    transcript = serialize_messages_to_transcript(live_messages)
    previous_summary = previous_summaries[-1] if previous_summaries else None
    return previous_summary, transcript


async def generate_summary_message_text(
    credential_manager: Any,
    summarized_messages: List[tuple[state.Message, Optional[state.Step]]],
    settings: CompactionSettings,
    current_model: Optional[str],
    current_temperature: Optional[float],
    current_reasoning_effort: Optional[str],
    capture_debug_payload: bool,
    provider_options: dict[str, Any],
) -> tuple[str, Optional[state.LLMUsageStats], Optional[Dict[str, Any]]]:
    previous_summary, transcript = _split_summary_inputs(summarized_messages)
    if current_model is None:
        return build_summary_message_text(summarized_messages, settings), None, None
    summary_model = settings.summary_model or current_model
    summary_provider = settings.summary_provider or None
    summary_temperature = (
        settings.summary_temperature
        if settings.summary_temperature is not None
        else current_temperature
    )
    summary_reasoning_effort = (
        settings.summary_reasoning_effort
        if settings.summary_reasoning_effort is not None
        else current_reasoning_effort
    )
    prompt = build_summary_generation_prompt(previous_summary, transcript, settings)
    request = connect.GenerateRequest(
        messages=[connect.UserMessage(content=prompt)],
        system_prompt=resolve_compaction_system_prompt(settings),
        temperature=summary_temperature,
        reasoning=(
            connect.ReasoningConfig(effort=summary_reasoning_effort)
            if summary_reasoning_effort is not None
            else None
        ),
    )
    debug_request_payload = None
    if capture_debug_payload:
        debug_request_payload = debug_capture_mod.build_debug_request_payload(
            request,
            resolve_compaction_system_prompt(settings),
            request.messages,
            None,
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
    except connect.ConnectError as exc:
        logger.warning(
            "Compaction summary generation failed",
            summary_model=summary_model,
            summary_provider=summary_provider,
            summarized_messages_count=len(summarized_messages),
            status_code=exc.error.status_code,
            code=exc.error.code,
            retryable=exc.error.retryable,
            err=exc,
        )
        if not exc.error.retryable:
            raise CompactionSummaryGenerationError(
                "Compaction summary generation failed",
                cause=exc,
                summary_model=summary_model,
                summary_provider=summary_provider,
            ) from exc
        return build_summary_message_text(summarized_messages, settings), None, None
    except Exception as exc:
        logger.warning(
            "Compaction summary generation failed",
            summary_model=summary_model,
            summary_provider=summary_provider,
            summarized_messages_count=len(summarized_messages),
            err=exc,
        )
        return build_summary_message_text(summarized_messages, settings), None, None
    summary_text = "".join(
        block.text for block in response.content if block.type == "text"
    ).strip()
    if not summary_text:
        return build_summary_message_text(summarized_messages, settings), None, None
    summary_body = build_summary_message_envelope(summary_text, settings)
    debug_payload = None
    if debug_request_payload is not None:
        debug_payload = debug_capture_mod.build_step_debug_payload(
            debug_request_payload,
            response,
        )
    return summary_body, _build_summary_usage(response, summary_model), debug_payload


async def maybe_compact_execution_history(
    history: Any,
    credential_manager: Any,
    execution: state.NodeExecution,
    preparation: CompactionPreparationResult,
) -> Optional[state.Step]:
    if not preparation.should_compact:
        return None
    prompt_messages = collect_prompt_messages(history, execution)
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
    summarized_messages = list(summarized_pairs)
    remaining_pairs = prompt_messages[summarize_count:]

    workflow_execution = execution._workflow_execution
    if workflow_execution is None:
        raise ValueError("NodeExecution is not attached to a workflow execution")

    summary_model = _resolve_summary_model_name(
        preparation.settings,
        preparation.current_model,
    )
    actual_prompt_tokens_before = _get_latest_prompt_tokens(prompt_messages)
    keep_recent_budget = (
        int(
            float(preparation.input_token_limit)
            * preparation.settings.keep_recent_ratio
        )
        if preparation.input_token_limit is not None
        else None
    )
    total_chars = _messages_character_count([message for message, _ in prompt_messages])
    logger.info(
        "Context compaction started",
        summary_model=summary_model,
        prompt_messages_before=len(prompt_messages),
        summarized_messages_count=len(summarized_messages),
        retained_messages_count=len(remaining_pairs),
        total_chars=total_chars,
        prompt_tokens_before=actual_prompt_tokens_before,
        input_token_limit=preparation.input_token_limit,
        trigger_threshold_tokens=(
            int(
                float(preparation.input_token_limit)
                * preparation.settings.trigger_threshold_ratio
            )
            if preparation.input_token_limit is not None
            else None
        ),
        keep_recent_budget_tokens=keep_recent_budget,
    )

    try:
        summary_text, summary_usage, summary_debug = (
            await generate_summary_message_text(
                credential_manager,
                summarized_messages,
                preparation.settings,
                preparation.current_model,
                preparation.current_temperature,
                preparation.current_reasoning_effort,
                preparation.capture_debug_payload,
                preparation.provider_options,
            )
        )
    except CompactionSummaryGenerationError as exc:
        logger.warning(
            "Context compaction finished",
            status="failed",
            summary_model=exc.summary_model,
            prompt_messages_before=len(prompt_messages),
            summarized_messages_count=len(summarized_messages),
            retained_messages_count=len(remaining_pairs),
            total_chars=total_chars,
            prompt_tokens_before=actual_prompt_tokens_before,
            status_code=exc.cause.error.status_code,
            code=exc.cause.error.code,
        )
        raise
    summary_message = state.Message(
        role=models.Role.ASSISTANT,
        text=summary_text,
    )
    history.upsert_message(workflow_execution, summary_message)
    prompt_token_delta = _build_compaction_prompt_usage_delta(
        actual_prompt_tokens_before,
        summary_usage,
        preparation.input_token_limit,
        preparation.settings,
    )
    _apply_compaction_prompt_usage_delta(
        history,
        workflow_execution,
        remaining_pairs,
        prompt_token_delta,
    )
    boundary_parent_step_id: Optional[UUID] = None
    first_retained_step: Optional[state.Step] = None
    for _, step in remaining_pairs:
        if step is not None:
            first_retained_step = step
            break
    if first_retained_step is not None:
        boundary_parent_step_id = first_retained_step.parent_step_id
    else:
        for _, step in reversed(summarized_pairs):
            if step is not None:
                boundary_parent_step_id = step.id
                break
    actual_prompt_tokens_after = _get_summary_output_tokens(summary_usage)
    summary_state = CompactionSummaryState(
        prompt_tokens_after=actual_prompt_tokens_after,
        summary_input_tokens=(
            int(summary_usage.prompt_tokens) if summary_usage is not None else None
        ),
        summary_output_tokens=(
            int(summary_usage.completion_tokens) if summary_usage is not None else None
        ),
        trigger_threshold_ratio=preparation.settings.trigger_threshold_ratio,
    )
    final_pairs = [(summary_message, None)] + remaining_pairs
    logger.info(
        "Context compaction finished",
        status="completed",
        summary_model=summary_model,
        prompt_messages_before=len(prompt_messages),
        prompt_messages_after=len(final_pairs),
        summarized_messages_count=len(summarized_messages),
        retained_messages_count=len(remaining_pairs),
        total_chars=total_chars,
        prompt_tokens_before=actual_prompt_tokens_before,
        prompt_tokens_after=actual_prompt_tokens_after,
        summary_chars=len(summary_text),
        summary_input_tokens=(
            int(summary_usage.prompt_tokens) if summary_usage is not None else None
        ),
        summary_output_tokens=(
            int(summary_usage.completion_tokens) if summary_usage is not None else None
        ),
    )

    compaction_count = 0
    selected_outcome = None
    if isinstance(execution.state, LLMExecutionState):
        selected_outcome = execution.state.selected_outcome
        if execution.state.compaction is not None:
            compaction_count = execution.state.compaction.compaction_count

    compaction_step = state.Step(
        workflow_execution=workflow_execution,
        execution_id=execution.id,
        parent_step_id=boundary_parent_step_id,
        type=state.StepType.CONTEXT_COMPACTION,
        message_id=summary_message.id,
        state=summary_state,
        llm_usage=summary_usage,
        debug=summary_debug,
        is_complete=True,
        is_auxiliary=True,
        is_final=True,
    )
    mutation_result = history.insert_step(
        workflow_execution,
        compaction_step,
        parent_step_id=boundary_parent_step_id,
        child_step_id=(
            first_retained_step.id if first_retained_step is not None else None
        ),
    )
    persisted_step = mutation_result.upserted_steps[0]
    execution.state = LLMExecutionState(
        selected_outcome=selected_outcome,
        compaction=LLMExecutionCompactionState(
            latest_compaction_step_id=persisted_step.id,
            compaction_count=compaction_count + 1,
            last_compaction_tokens_before=preparation.estimated_context_tokens,
            last_compaction_summary_input_tokens=(
                int(summary_usage.prompt_tokens) if summary_usage is not None else None
            ),
        ),
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

    latest_prompt_tokens = _get_latest_prompt_tokens(prompt_messages)
    if latest_prompt_tokens is None:
        return max(1, len(prompt_messages) - 1)

    keep_recent_budget = max(
        1, int(float(input_token_limit) * settings.keep_recent_ratio)
    )
    target_cut_prompt_tokens = max(0, latest_prompt_tokens - keep_recent_budget)
    primary_candidates: List[tuple[int, int]] = []
    fallback_candidates: List[tuple[int, int]] = []
    latest_cut_prompt_tokens: Optional[int] = None

    for index, (message, step) in enumerate(prompt_messages):
        prompt_tokens = _get_pair_prompt_tokens(message, step)
        if prompt_tokens is not None:
            latest_cut_prompt_tokens = prompt_tokens
            continue
        if index <= 0 or latest_cut_prompt_tokens is None:
            continue
        if _is_primary_cut_boundary(message, step):
            primary_candidates.append((index, latest_cut_prompt_tokens))
            continue
        if _is_fallback_cut_boundary(message, step):
            fallback_candidates.append((index, latest_cut_prompt_tokens))

    candidate_pool = primary_candidates or fallback_candidates
    if not candidate_pool:
        return max(1, len(prompt_messages) - 1)

    target_or_lower_candidates = [
        candidate
        for candidate in candidate_pool
        if candidate[1] <= target_cut_prompt_tokens
    ]
    if target_or_lower_candidates:
        selected_index, _ = max(target_or_lower_candidates, key=lambda item: item[1])
        return _adjust_cut_index_for_tool_round(prompt_messages, selected_index)

    selected_index, _ = min(candidate_pool, key=lambda item: item[1])
    return _adjust_cut_index_for_tool_round(prompt_messages, selected_index)


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
