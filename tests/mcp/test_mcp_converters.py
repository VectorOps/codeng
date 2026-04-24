import pytest

from vocode.mcp.converters import MCPConversionError
from vocode.mcp.converters import normalize_prompt_descriptor
from vocode.mcp.converters import normalize_resource_descriptor
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


def test_normalize_tool_descriptor_rejects_non_object_schema_type() -> None:
    with pytest.raises(MCPConversionError, match="type=object"):
        normalize_tool_descriptor(
            "local",
            {
                "name": "search",
                "inputSchema": {"type": "string"},
            },
        )


def test_normalize_tool_descriptor_rejects_non_object_annotations() -> None:
    with pytest.raises(MCPConversionError, match="annotations"):
        normalize_tool_descriptor(
            "local",
            {
                "name": "search",
                "annotations": "invalid",
            },
        )


def test_normalize_prompt_descriptor_preserves_argument_metadata() -> None:
    descriptor = normalize_prompt_descriptor(
        "local",
        {
            "name": "summarize",
            "description": "Summarize input",
            "arguments": [
                {
                    "name": "topic",
                    "description": "Topic to summarize",
                    "required": True,
                }
            ],
        },
    )

    assert descriptor.source_name == "local"
    assert descriptor.prompt_name == "summarize"
    assert descriptor.arguments[0].name == "topic"
    assert descriptor.arguments[0].required is True


def test_normalize_prompt_descriptor_rejects_invalid_arguments() -> None:
    with pytest.raises(MCPConversionError, match="arguments"):
        normalize_prompt_descriptor(
            "local",
            {
                "name": "summarize",
                "arguments": ["bad"],
            },
        )


def test_normalize_resource_descriptor_preserves_metadata() -> None:
    descriptor = normalize_resource_descriptor(
        "local",
        {
            "uri": "file:///docs/readme.md",
            "name": "readme",
            "mimeType": "text/markdown",
            "annotations": {"audience": "internal"},
        },
    )

    assert descriptor.source_name == "local"
    assert descriptor.uri == "file:///docs/readme.md"
    assert descriptor.mime_type == "text/markdown"
    assert descriptor.annotations == {"audience": "internal"}


def test_normalize_resource_descriptor_rejects_missing_uri() -> None:
    with pytest.raises(MCPConversionError, match="non-empty uri"):
        normalize_resource_descriptor("local", {"name": "readme"})
