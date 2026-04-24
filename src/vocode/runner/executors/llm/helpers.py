from __future__ import annotations

from typing import Any, Dict, List, Optional, Final
import re
import contextlib

import connect

from vocode.logger import logger
from vocode.settings import ToolSpec  # type: ignore

from .models import LLMNode

# Constants and regexes
CHOOSE_OUTCOME_TOOL_NAME: Final[str] = "__choose_outcome__"
OUTCOME_TAG_RE = re.compile(r"^\s*OUTCOME\s*:\s*([A-Za-z0-9_\-]+)\s*$")
OUTCOME_LINE_PREFIX_RE = re.compile(r"^\s*OUTCOME\s*:\s*")


def build_system_prompt(cfg: LLMNode) -> Optional[str]:
    system_parts: List[str] = []
    if cfg.system:
        system_parts.append(cfg.system)
    if cfg.system_append:
        system_parts.append(cfg.system_append)
    outcome_names = get_outcome_names(cfg)
    if len(outcome_names) > 1:
        outcome_desc_bullets = get_outcome_desc_bullets(cfg)
        outcome_instruction = build_outcome_system_instruction(
            cfg,
            outcome_desc_bullets,
        )
        system_parts.append(outcome_instruction)
    system_prompt = "\n\n".join(part for part in system_parts if part).strip()
    return system_prompt or None


# Outcome helpers
def parse_outcome_from_text(text: str, valid_outcomes: List[str]) -> Optional[str]:
    for line in text.splitlines()[::-1]:
        m = OUTCOME_TAG_RE.match(line.strip())
        if m:
            cand = m.group(1)
            if cand in valid_outcomes:
                return cand
    return None


def strip_outcome_line(text: str, outcome_name: str) -> str:
    lines = text.splitlines()
    target_line = f"OUTCOME: {outcome_name}"
    remove_index: Optional[int] = None
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].strip() == target_line:
            remove_index = index
            break
    if remove_index is None:
        return text.rstrip()
    updated_lines = [line for index, line in enumerate(lines) if index != remove_index]
    return "\n".join(updated_lines).rstrip()


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
        "You must choose exactly one outcome before finishing. Consider the available outcomes and pick the best fit based on the conversation:\n"
        f"{outcome_desc_bullets}\n\n"
        "After producing your final answer, append a last line exactly as:\n"
        f"OUTCOME: <one of {outcomes}>\n"
        "Only output the outcome name on that line and nothing else. Do not finish without including that line."
    )


def build_function_system_instruction(
    outcomes: List[str],
    outcome_desc_bullets: str,
) -> str:
    return (
        "You must choose exactly one outcome before finishing. Consider the available outcomes and pick the best fit based on the conversation:\n"
        f"{outcome_desc_bullets}\n\n"
        f"Use the tool {CHOOSE_OUTCOME_TOOL_NAME} to select one of {outcomes}. "
        "If you need other tools, use them first, then call the outcome tool before your final response. "
        "Do not finish without selecting an outcome."
    )


def _format_outcome_template(
    template: str,
    outcome_names: List[str],
    outcome_desc_bullets: str,
) -> str:
    format_values = {
        "outcome_names": outcome_names,
        "outcome_list": ", ".join(outcome_names),
        "outcome_desc_bullets": outcome_desc_bullets,
        "choose_outcome_tool_name": CHOOSE_OUTCOME_TOOL_NAME,
    }
    with contextlib.suppress(Exception):
        return template.format(**format_values).strip()
    return template.strip()


def build_outcome_system_instruction(
    cfg: LLMNode,
    outcome_desc_bullets: str,
) -> str:
    outcome_names = get_outcome_names(cfg)
    if cfg.outcome_selection_instruction:
        return _format_outcome_template(
            cfg.outcome_selection_instruction,
            outcome_names,
            outcome_desc_bullets,
        )
    if cfg.outcome_strategy == "tag":
        return build_tag_system_instruction(outcome_names, outcome_desc_bullets)
    return build_function_system_instruction(outcome_names, outcome_desc_bullets)


def build_missing_outcome_retry_instruction(cfg: LLMNode) -> str:
    outcome_names = get_outcome_names(cfg)
    outcome_desc_bullets = get_outcome_desc_bullets(cfg)
    if cfg.outcome_retry_instruction:
        return _format_outcome_template(
            cfg.outcome_retry_instruction,
            outcome_names,
            outcome_desc_bullets,
        )
    if cfg.outcome_strategy == "tag":
        return (
            "You must provide the final response again AND choose an outcome. "
            f"Choose exactly one of: {', '.join(outcome_names)}. "
            "Append a final line exactly as OUTCOME: <outcome_name>."
        )
    return (
        "You must provide the final response again AND choose an outcome. "
        f"Choose exactly one of: {', '.join(outcome_names)} by calling the tool {CHOOSE_OUTCOME_TOOL_NAME}."
    )


def build_outcome_tool_result_text(
    outcome_name: Optional[str],
    valid_outcomes: List[str],
) -> str:
    if outcome_name is not None and outcome_name in valid_outcomes:
        return "outcome accepted"
    return "invalid outcome, possible options: " + ", ".join(valid_outcomes)


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
    - Global .skip_listing overrides node .skip_listing when provided.
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

            # skip_listing: global overrides node when provided
            if isinstance(gspec.skip_listing, bool):
                base.skip_listing = gspec.skip_listing

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
