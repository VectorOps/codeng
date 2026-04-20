from __future__ import annotations

from typing import Any, Dict

from vocode.mcp import models as mcp_models


class MCPConversionError(Exception):
    pass


def normalize_tool_descriptor(
    source_name: str,
    payload: Dict[str, Any],
) -> mcp_models.MCPToolDescriptor:
    tool_name = payload.get("name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise MCPConversionError("mcp tool payload must include a non-empty name")
    input_schema = payload.get("inputSchema")
    if input_schema is None:
        input_schema = {"type": "object", "properties": {}}
    if not isinstance(input_schema, dict):
        raise MCPConversionError("mcp tool inputSchema must be an object when present")
    return mcp_models.MCPToolDescriptor(
        source_name=source_name,
        tool_name=tool_name,
        title=payload.get("title"),
        description=payload.get("description"),
        input_schema=input_schema,
        annotations=(payload.get("annotations") or {}),
    )
