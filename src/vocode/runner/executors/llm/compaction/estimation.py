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
    # Token estimation is reserved for internal compaction decisions only.
    # Never surface estimated token counts in user-facing UI or logs.
    return sum(estimate_message_tokens(message) for message, _ in prompt_messages)


def get_threshold_context_tokens(
    prompt_messages: List[tuple[state.Message, Optional[state.Step]]],
    estimated_context_tokens: int,
) -> int:
    for message, _ in reversed(prompt_messages):
        if message.llm_usage is None:
            continue
        usage = message.llm_usage
        return int(usage.prompt_tokens)
    return estimated_context_tokens


def should_trigger_compaction(
    settings: CompactionSettings,
    input_token_limit: Optional[int],
    threshold_context_tokens: int,
) -> bool:
    if not settings.enabled:
        return False
    if input_token_limit is None or input_token_limit <= 0:
        return False
    return threshold_context_tokens >= (
        float(input_token_limit) * settings.trigger_threshold_ratio
    )
