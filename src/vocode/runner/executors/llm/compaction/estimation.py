from __future__ import annotations

from math import ceil
import json
from typing import List, Optional

from vocode import state
from .models import CompactionSettings


def _estimate_text_tokens(text: Optional[str]) -> int:
    if not text:
        return 0
    return max(1, ceil(len(text) / 4))


def estimate_message_tokens(message: state.Message) -> int:
    total = _estimate_text_tokens(message.text)
    total += _estimate_text_tokens(message.thinking_content)
    for req in message.tool_call_requests:
        total += _estimate_text_tokens(req.name)
        total += _estimate_text_tokens(json.dumps(req.arguments, sort_keys=True))
    for resp in message.tool_call_responses:
        total += _estimate_text_tokens(resp.name)
        if resp.result is not None:
            total += _estimate_text_tokens(json.dumps(resp.result, sort_keys=True))
    return total


def estimate_context_tokens(
    prompt_messages: List[tuple[state.Message, Optional[state.Step]]],
) -> int:
    latest_usage_index: Optional[int] = None
    latest_usage_total: Optional[int] = None
    for index in range(len(prompt_messages) - 1, -1, -1):
        _, step = prompt_messages[index]
        if step is None:
            continue
        if step.llm_usage is None:
            continue
        latest_usage_index = index
        latest_usage_total = int(step.llm_usage.prompt_tokens) + int(
            step.llm_usage.completion_tokens
        )
        break
    if latest_usage_index is None or latest_usage_total is None:
        return sum(estimate_message_tokens(message) for message, _ in prompt_messages)
    trailing_total = 0
    for message, _ in prompt_messages[latest_usage_index + 1 :]:
        trailing_total += estimate_message_tokens(message)
    return latest_usage_total + trailing_total


def should_trigger_compaction(
    settings: CompactionSettings,
    input_token_limit: Optional[int],
    estimated_context_tokens: int,
) -> bool:
    if not settings.enabled:
        return False
    if input_token_limit is None or input_token_limit <= 0:
        return False
    return estimated_context_tokens >= (
        float(input_token_limit) * settings.trigger_threshold_ratio
    )
