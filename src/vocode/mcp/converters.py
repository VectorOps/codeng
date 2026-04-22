from __future__ import annotations

from typing import Any, Dict, List

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
    schema_type = input_schema.get("type")
    if schema_type is not None and schema_type != "object":
        raise MCPConversionError("mcp tool inputSchema must declare type=object")
    annotations = payload.get("annotations") or {}
    if not isinstance(annotations, dict):
        raise MCPConversionError("mcp tool annotations must be an object when present")
    return mcp_models.MCPToolDescriptor(
        source_name=source_name,
        tool_name=tool_name,
        title=payload.get("title"),
        description=payload.get("description"),
        input_schema=input_schema,
        annotations=annotations,
    )


def normalize_prompt_descriptor(
    source_name: str,
    payload: Dict[str, Any],
) -> mcp_models.MCPPromptDescriptor:
    prompt_name = payload.get("name")
    if not isinstance(prompt_name, str) or not prompt_name.strip():
        raise MCPConversionError("mcp prompt payload must include a non-empty name")
    raw_arguments = payload.get("arguments") or []
    if not isinstance(raw_arguments, list):
        raise MCPConversionError("mcp prompt arguments must be a list when present")
    arguments: List[mcp_models.MCPPromptArgumentDescriptor] = []
    for item in raw_arguments:
        if not isinstance(item, dict):
            raise MCPConversionError("mcp prompt arguments must contain objects")
        argument_name = item.get("name")
        if not isinstance(argument_name, str) or not argument_name.strip():
            raise MCPConversionError(
                "mcp prompt arguments must include a non-empty name"
            )
        arguments.append(
            mcp_models.MCPPromptArgumentDescriptor(
                name=argument_name,
                description=item.get("description"),
                required=bool(item.get("required", False)),
            )
        )
    return mcp_models.MCPPromptDescriptor(
        source_name=source_name,
        prompt_name=prompt_name,
        title=payload.get("title"),
        description=payload.get("description"),
        arguments=arguments,
    )


def normalize_resource_descriptor(
    source_name: str,
    payload: Dict[str, Any],
) -> mcp_models.MCPResourceDescriptor:
    uri = payload.get("uri")
    if not isinstance(uri, str) or not uri.strip():
        raise MCPConversionError("mcp resource payload must include a non-empty uri")
    annotations = payload.get("annotations") or {}
    if not isinstance(annotations, dict):
        raise MCPConversionError("mcp resource annotations must be an object")
    return mcp_models.MCPResourceDescriptor(
        source_name=source_name,
        uri=uri,
        name=payload.get("name"),
        title=payload.get("title"),
        description=payload.get("description"),
        mime_type=payload.get("mimeType"),
        annotations=annotations,
    )
