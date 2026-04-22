from vocode.mcp import naming as mcp_naming


def test_build_internal_tool_name_normalizes_source_and_tool_names() -> None:
    assert (
        mcp_naming.build_internal_tool_name("local.dev", "search docs")
        == "mcp__local_dev__search_docs"
    )


def test_parse_internal_tool_name_returns_source_and_tool_names() -> None:
    parsed = mcp_naming.parse_internal_tool_name("mcp__local__search")

    assert parsed is not None
    assert parsed.source_name == "local"
    assert parsed.tool_name == "search"


def test_parse_internal_tool_name_rejects_invalid_values() -> None:
    assert mcp_naming.parse_internal_tool_name("search") is None
    assert mcp_naming.parse_internal_tool_name("mcp__local") is None


def test_normalize_tool_ref_returns_normalized_segments() -> None:
    normalized = mcp_naming.normalize_tool_ref("local.dev", "search docs")

    assert normalized.source_name == "local_dev"
    assert normalized.tool_name == "search_docs"


def test_describe_internal_tool_naming_contract_mentions_normalization_and_collisions() -> (
    None
):
    description = mcp_naming.describe_internal_tool_naming_contract()

    assert "normalized" in description
    assert "not recoverable" in description
    assert "collision" in description
