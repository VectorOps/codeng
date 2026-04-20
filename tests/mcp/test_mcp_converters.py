import pytest

from vocode.mcp.converters import MCPConversionError
from vocode.mcp.converters import normalize_tool_descriptor


def test_normalize_tool_descriptor_defaults_parameterless_schema() -> None:
    descriptor = normalize_tool_descriptor(
        "local",
        {
            "name": "search",
            "description": "Search docs",
        },
    )

    assert descriptor.source_name == "local"
    assert descriptor.tool_name == "search"
    assert descriptor.input_schema == {"type": "object", "properties": {}}


def test_normalize_tool_descriptor_preserves_input_schema_and_annotations() -> None:
    descriptor = normalize_tool_descriptor(
        "local",
        {
            "name": "search",
            "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
            "annotations": {"readOnlyHint": True},
        },
    )

    assert descriptor.input_schema["properties"]["q"]["type"] == "string"
    assert descriptor.annotations == {"readOnlyHint": True}


def test_normalize_tool_descriptor_rejects_missing_name() -> None:
    with pytest.raises(MCPConversionError, match="non-empty name"):
        normalize_tool_descriptor("local", {"description": "missing name"})


def test_normalize_tool_descriptor_rejects_non_object_input_schema() -> None:
    with pytest.raises(MCPConversionError, match="inputSchema"):
        normalize_tool_descriptor(
            "local",
            {
                "name": "search",
                "inputSchema": "invalid",
            },
        )
