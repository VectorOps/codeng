from __future__ import annotations

import re

from typing import Optional

from pydantic import BaseModel


_MCP_INTERNAL_TOOL_PREFIX = "mcp__"
_MCP_INTERNAL_TOOL_SAFE_CHARS = re.compile(r"[^A-Za-z0-9_]+")


class MCPInternalToolRef(BaseModel):
    source_name: str
    tool_name: str


class MCPNormalizedToolRef(BaseModel):
    source_name: str
    tool_name: str


def build_internal_tool_name(source_name: str, tool_name: str) -> str:
    normalized_ref = normalize_tool_ref(source_name, tool_name)
    normalized_source = normalized_ref.source_name
    normalized_tool = normalized_ref.tool_name
    return f"{_MCP_INTERNAL_TOOL_PREFIX}{normalized_source}" f"__{normalized_tool}"


def normalize_tool_ref(source_name: str, tool_name: str) -> MCPNormalizedToolRef:
    return MCPNormalizedToolRef(
        source_name=_normalize_segment(source_name),
        tool_name=_normalize_segment(tool_name),
    )


def parse_internal_tool_name(value: str) -> Optional[MCPInternalToolRef]:
    if not value.startswith(_MCP_INTERNAL_TOOL_PREFIX):
        return None
    remainder = value[len(_MCP_INTERNAL_TOOL_PREFIX) :]
    source_name, separator, tool_name = remainder.partition("__")
    if separator == "" or source_name == "" or tool_name == "":
        return None
    return MCPInternalToolRef(source_name=source_name, tool_name=tool_name)


def describe_internal_tool_naming_contract() -> str:
    return (
        "MCP internal tool names use the format 'mcp__<source>__<tool>' with "
        "each segment normalized to ASCII letters, digits, and underscores. "
        "Reverse lookup returns normalized source and tool segments only, so the "
        "original remote names are not recoverable from the internal name alone. "
        "If multiple remote tools normalize to the same internal name within a "
        "source, they must be treated as a collision and excluded from "
        "materialization."
    )


def _normalize_segment(value: str) -> str:
    normalized = _MCP_INTERNAL_TOOL_SAFE_CHARS.sub("_", value)
    normalized = normalized.strip("_")
    if normalized == "":
        return "tool"
    return normalized
