from __future__ import annotations

import json
from typing import List, Optional

from vocode import state
from .models import CompactionSettings

DEFAULT_COMPACTION_SYSTEM_PROMPT = (
    "You are maintaining a continuation checkpoint for a coding workflow. "
    "Produce a compact but precise summary another LLM can resume from safely. "
    "Preserve exact file paths, tool names, identifiers, node names, outcome names, "
    "and error text when relevant. Do not invent progress."
)

DEFAULT_COMPACTION_INSTRUCTIONS = (
    "Return markdown with exactly these sections:\n"
    "## Goal\n"
    "## Constraints & Preferences\n"
    "## Progress\n"
    "### Done\n"
    "### In Progress\n"
    "### Blocked\n"
    "## Key Decisions\n"
    "## Next Steps\n"
    "## Critical Context\n\n"
    "Rules:\n"
    "- Prefer concrete facts over abstraction.\n"
    "- Keep file paths and tool names exact.\n"
    "- Keep error messages exact or minimally trimmed.\n"
    "- Move items to Done only if the transcript shows completion.\n"
    "- Keep Next Steps actionable and short.\n"
    "- If information is unknown, omit it instead of guessing."
)

DEFAULT_COMPACTION_UPDATE_INSTRUCTIONS = (
    "Update the existing summary instead of rewriting from scratch.\n"
    "Rules:\n"
    "- Preserve prior facts unless the new transcript supersedes them.\n"
    "- Move completed items from In Progress to Done.\n"
    "- Remove stale Next Steps that are already complete.\n"
    "- Keep persistent constraints and preferences unless contradicted.\n"
    "- Preserve exact paths, identifiers, tool names, and errors."
)

SUMMARY_PROMPT_FOCUS = (
    "Prioritize these details when present:\n"
    "- current user goal\n"
    "- explicit constraints or preferences\n"
    "- files read or changed\n"
    "- tools used and why\n"
    "- important command or tool outputs\n"
    "- exact errors and blockers\n"
    "- decisions already made\n"
    "- concrete next steps"
)


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
    parts: List[str] = [instructions, SUMMARY_PROMPT_FOCUS]
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
        if message.text.strip():
            lines.append(f"[{role_name}]")
            lines.append(message.text.strip())
        if message.thinking_content:
            lines.append(f"[{role_name} thinking]")
            lines.append(message.thinking_content.strip())
        if message.tool_call_requests:
            lines.append("[Assistant tool calls]")
            tool_lines = []
            for req in message.tool_call_requests:
                tool_lines.append(
                    f"- {req.name}({json.dumps(req.arguments, sort_keys=True)})"
                )
            lines.extend(tool_lines)
        if message.tool_call_responses:
            lines.append("[Tool results]")
        for resp in message.tool_call_responses:
            result_text = ""
            if resp.result is not None:
                result_text = json.dumps(resp.result, sort_keys=True)
            lines.append(f"- {resp.name}: {result_text or '[empty]'}")
        if lines and lines[-1] != "":
            lines.append("")
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
