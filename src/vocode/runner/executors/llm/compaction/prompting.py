from __future__ import annotations

import json
from typing import List, Optional

from vocode import state
from .models import CompactionSettings

DEFAULT_COMPACTION_SYSTEM_PROMPT = (
    "Produce a structured continuation checkpoint for a long-running workflow. "
    "Preserve the user's goal, constraints, progress, key decisions, next steps, "
    "and exact file paths, tool names, identifiers, and error text when relevant."
)

DEFAULT_COMPACTION_INSTRUCTIONS = (
    "Use the required markdown sections and keep the summary concise, factual, and "
    "optimized for another LLM continuing the same task."
)

DEFAULT_COMPACTION_UPDATE_INSTRUCTIONS = (
    "Update the existing summary instead of rewriting history from scratch. Preserve "
    "earlier facts unless superseded, move progress from in-progress to done when "
    "appropriate, keep exact file paths, identifiers, tool names, and error text, "
    "and refresh next steps to match the latest transcript."
)


def _truncate_text(text: str, limit: int = 400) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def build_compaction_instructions(custom_instructions: Optional[str]) -> str:
    if custom_instructions:
        return DEFAULT_COMPACTION_INSTRUCTIONS + "\n\n" + custom_instructions.strip()
    return DEFAULT_COMPACTION_INSTRUCTIONS


def build_summary_generation_prompt(
    previous_summary: Optional[str],
    transcript: str,
    settings: CompactionSettings,
) -> str:
    instructions = resolve_compaction_instructions(settings)
    parts: List[str] = [instructions]
    if previous_summary:
        parts.extend(
            [
                DEFAULT_COMPACTION_UPDATE_INSTRUCTIONS,
                "Previous summary:",
                previous_summary,
            ]
        )
    if transcript:
        parts.extend(
            [
                "Transcript to compact:",
                transcript,
            ]
        )
    return "\n\n".join(part for part in parts if part)


def resolve_compaction_system_prompt(settings: CompactionSettings) -> str:
    if settings.prompt_system:
        return settings.prompt_system.strip()
    return DEFAULT_COMPACTION_SYSTEM_PROMPT


def resolve_compaction_instructions(settings: CompactionSettings) -> str:
    return build_compaction_instructions(settings.prompt_instructions)


def serialize_messages_to_transcript(messages: List[state.Message]) -> str:
    lines: List[str] = []
    for message in messages:
        role_name = message.role.value.capitalize()
        if message.text:
            lines.append(f"[{role_name}]: {_truncate_text(message.text)}")
        if message.thinking_content:
            lines.append(
                f"[{role_name} thinking]: {_truncate_text(message.thinking_content)}"
            )
        if message.tool_call_requests:
            tool_lines = []
            for req in message.tool_call_requests:
                tool_lines.append(
                    f"{req.name}({json.dumps(req.arguments, sort_keys=True)})"
                )
            lines.append("[Assistant tool calls]: " + "; ".join(tool_lines))
        for resp in message.tool_call_responses:
            result_text = ""
            if resp.result is not None:
                result_text = json.dumps(resp.result, sort_keys=True)
            lines.append(
                f"[Tool result {resp.name}]: {_truncate_text(result_text or '[empty]')}"
            )
    return "\n".join(lines)


def extract_wrapped_summary_text(message_text: str) -> Optional[str]:
    start_tag = "<summary>"
    end_tag = "</summary>"
    start_index = message_text.find(start_tag)
    end_index = message_text.rfind(end_tag)
    if start_index < 0 or end_index < 0 or end_index <= start_index:
        return None
    inner = message_text[start_index + len(start_tag) : end_index]
    return inner.strip() or None
