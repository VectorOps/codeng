from __future__ import annotations

from typing import Any, Dict, List, Optional, Final
import re
import contextlib
import json

import connect

from vocode.state import Message
from vocode.logger import logger
from vocode.settings import ToolSpec  # type: ignore

from .models import LLMNode

# Constants and regexes
CHOOSE_OUTCOME_TOOL_NAME: Final[str] = "__choose_outcome__"
OUTCOME_TAG_RE = re.compile(r"^\s*OUTCOME\s*:\s*([A-Za-z0-9_\-]+)\s*$")
OUTCOME_LINE_PREFIX_RE = re.compile(r"^\s*OUTCOME\s*:\s*")
MAX_ROUNDS: Final[int] = 32


def build_system_prompt(cfg: LLMNode) -> Optional[str]:
    system_parts: List[str] = []
    if cfg.system:
        system_parts.append(cfg.system)
    if cfg.system_append:
        system_parts.append(cfg.system_append)
    outcome_names = get_outcome_names(cfg)
    if len(outcome_names) > 1 and cfg.outcome_strategy == "tag":
        outcome_desc_bullets = get_outcome_desc_bullets(cfg)
        tag_instruction = build_tag_system_instruction(
            outcome_names,
            outcome_desc_bullets,
        )
        system_parts.append(tag_instruction)
    system_prompt = "\n\n".join(part for part in system_parts if part).strip()
    return system_prompt or None


def serialize_connect_messages(
    system_prompt: Optional[str],
    messages: List[connect.Message],
) -> List[Dict[str, Any]]:
    serialized_messages: List[Dict[str, Any]] = []

    if system_prompt:
        serialized_messages.append(
            {
                "role": "system",
                "content": system_prompt,
            }
        )

    for message in messages:
        if message.role == "user":
            content = message.content
            if isinstance(content, str):
                text = content
            else:
                text = "".join(
                    block.text for block in content if block.type == "text"
                )
            serialized_messages.append(
                {
                    "role": "user",
                    "content": text,
                }
            )
            continue

        if message.role == "assistant":
            base: Dict[str, Any] = {
                "role": "assistant",
                "content": "".join(
                    block.text for block in message.content if block.type == "text"
                ),
            }

            provider_specific_fields = _metadata_to_provider_specific_fields(
                provider_meta=message.provider_meta,
                protocol_state=message.protocol_meta,
                reasoning_signature=_assistant_reasoning_signature(message),
            )
            if provider_specific_fields is not None:
                base["provider_specific_fields"] = provider_specific_fields

            tool_calls: List[Dict[str, Any]] = []
            for block in message.content:
                if block.type != "tool_call":
                    continue
                tool_call: Dict[str, Any] = {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.arguments),
                    },
                }
                provider_specific_fields = _metadata_to_provider_specific_fields(
                    provider_meta=block.provider_meta,
                    protocol_state=block.protocol_meta,
                    reasoning_signature=_annotation_reasoning_signature(
                        block.annotations
                    ),
                )
                if provider_specific_fields is not None:
                    tool_call["provider_specific_fields"] = provider_specific_fields
                tool_calls.append(tool_call)

            if tool_calls:
                base["tool_calls"] = tool_calls

            serialized_messages.append(base)
            continue

        serialized_messages.append(
            {
                "role": "tool",
                "type": "function_call",
                "tool_call_id": message.tool_call_id,
                "name": message.tool_name,
                "content": "".join(
                    block.text for block in message.content if block.type == "text"
                ),
            }
        )

    return serialized_messages


def _assistant_reasoning_signature(
    message: connect.AssistantMessage,
) -> Optional[str]:
    for block in message.content:
        if block.type == "reasoning" and block.signature:
            return block.signature
    return None


def _annotation_reasoning_signature(
    annotations: Any,
) -> Optional[str]:
    if isinstance(annotations, dict):
        value = annotations.get("reasoning_signature")
        if isinstance(value, str) and value:
            return value
    return None


def _metadata_to_provider_specific_fields(
    *,
    provider_meta: Dict[str, Any],
    protocol_state: Dict[str, Any],
    reasoning_signature: Optional[str],
) -> Optional[Dict[str, Any]]:
    payload: Dict[str, Any] = {}
    if provider_meta:
        payload["provider_meta"] = dict(provider_meta)
    if protocol_state:
        payload["protocol_state"] = dict(protocol_state)
    if reasoning_signature:
        payload["reasoning_signature"] = reasoning_signature
    return payload or None


# Message mapping and prompt building
def map_message_to_llm_dict(m: Message, cfg: LLMNode) -> Dict[str, Any]:
    role = m.role or "user"
    is_external = m.node is None or m.node != cfg.name
    if is_external:
        role = "user"
    else:
        if role == "agent":
            role = "assistant"
        elif role not in ("user", "system", "tool", "assistant"):
            role = "user"
    return {"role": role, "content": m.text}


# Outcome helpers
def parse_outcome_from_text(text: str, valid_outcomes: List[str]) -> Optional[str]:
    for line in text.splitlines()[::-1]:
        m = OUTCOME_TAG_RE.match(line.strip())
        if m:
            cand = m.group(1)
            if cand in valid_outcomes:
                return cand
    return None


def strip_outcome_line(text: str) -> str:
    return "\n".join(
        [ln for ln in text.splitlines() if not OUTCOME_LINE_PREFIX_RE.match(ln.strip())]
    ).rstrip()


def build_choose_outcome_tool(
    outcomes: List[str],
    outcome_desc_bullets: str,
    outcome_choice_desc: str,
) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": CHOOSE_OUTCOME_TOOL_NAME,
            "description": "Selects the conversation outcome to take next. Available outcomes:\n"
            + outcome_desc_bullets,
            "parameters": {
                "type": "object",
                "properties": {
                    "outcome": {
                        "type": "string",
                        "enum": outcomes,
                        "description": outcome_choice_desc,
                    }
                },
                "required": ["outcome"],
            },
        },
    }


def build_tag_system_instruction(
    outcomes: List[str],
    outcome_desc_bullets: str,
) -> str:
    return (
        "Consider the available outcomes and pick the best fit based on the conversation:\n"
        f"{outcome_desc_bullets}\n\n"
        "After producing your final answer, append a last line exactly as:\n"
        f"OUTCOME: <one of {outcomes}>\n"
        "Only output the outcome name on that line and nothing else."
    )


def get_outcome_names(cfg: LLMNode) -> List[str]:
    return [s.name for s in (cfg.outcomes or [])]


def get_outcome_desc_bullets(cfg: LLMNode) -> str:
    lines: List[str] = []
    for s in cfg.outcomes or []:
        desc = s.description or ""
        lines.append(f"- {s.name}: {desc}".rstrip())
    return "\n".join(lines)


def get_outcome_choice_desc(cfg: LLMNode, outcome_desc_bullets: str) -> str:
    if outcome_desc_bullets.strip():
        return "Choose exactly one of the following outcomes:\n" + outcome_desc_bullets
    return "Choose the appropriate outcome."


def build_effective_tool_specs(project: Any, cfg: LLMNode) -> Dict[str, ToolSpec]:
    """Merge node-level ToolSpec with project-level (global) ToolSpec by name.

    Precedence rules:
    - Global .enabled overrides node .enabled when provided.
    - Global .auto_approve overrides node .auto_approve when non-None.
    - Global .auto_approve_rules extend node .auto_approve_rules.
    - .config is merged shallowly: node.config first, then global.config.

    This helper constructs the effective ToolSpec via model_copy/update so new
    fields added to ToolSpec are preserved by default and do not need special
    handling here.
    Only returns specs for tools listed on this node.
    """
    global_specs: Dict[str, ToolSpec] = {}
    try:
        settings_tools = (
            (project.settings.tools or []) if project and project.settings else []
        )
        for ts in settings_tools:
            global_specs[ts.name] = ts
    except Exception:
        global_specs = {}

    effective: Dict[str, ToolSpec] = {}
    for node_spec in cfg.tools or []:
        gspec = global_specs.get(node_spec.name)

        # Start from a shallow copy of the node spec so any future fields on
        # ToolSpec are preserved automatically.
        base = node_spec.model_copy(deep=True)

        if gspec is not None:
            # enabled: global overrides node when provided
            if isinstance(gspec.enabled, bool):
                base.enabled = gspec.enabled

            # auto_approve: global wins when explicitly set
            if gspec.auto_approve is not None:
                base.auto_approve = gspec.auto_approve

            # auto_approve_rules: concatenate node + global so that both
            # scopes can contribute matchers.
            if getattr(gspec, "auto_approve_rules", None):
                # Ensure list exists on base
                base.auto_approve_rules = list(base.auto_approve_rules or [])
                base.auto_approve_rules.extend(gspec.auto_approve_rules)

            # config: node first, then global (global overrides on conflicts)
            merged_cfg: Dict[str, Any] = {}
            merged_cfg.update(node_spec.config or {})
            merged_cfg.update(gspec.config or {})
            base.config = merged_cfg

        effective[node_spec.name] = base
    return effective


def resolve_model_token_limit(cfg: LLMNode) -> Optional[int]:
    """Resolve the model input context window (prompt token limit).

    Fallback order:
    1) Connect model registry context_window
    2) cfg.extra['model_max_tokens']
    3) Connect model registry max_output_tokens
    """
    with contextlib.suppress(Exception):
        model = connect.default_model_registry.resolve(cfg.model)
        if model.context_window is not None and model.context_window > 0:
            return int(model.context_window)
    with contextlib.suppress(Exception):
        v = int((cfg.extra or {}).get("model_max_tokens") or 0)
        if v > 0:
            return v
    with contextlib.suppress(Exception):
        model = connect.default_model_registry.resolve(cfg.model)
        if model.max_output_tokens is not None and model.max_output_tokens > 0:
            return int(model.max_output_tokens)
    return None
