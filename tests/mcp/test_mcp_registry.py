from vocode.mcp.registry import MCPRegistry
from vocode.settings import MCPExternalSourceSettings
from vocode.settings import MCPRootEntry
from vocode.settings import MCPRootSettings
from vocode.settings import MCPSettings
from vocode.settings import MCPStdioSourceSettings


def _make_settings() -> MCPSettings:
    return MCPSettings(
        roots=MCPRootSettings(
            entries=[MCPRootEntry(uri="file:///global", name="global")]
        ),
        sources={
            "local": MCPStdioSourceSettings(
                command="uvx",
                roots=MCPRootSettings(
                    entries=[MCPRootEntry(uri="file:///source", name="source")]
                ),
            )
        },
    )


def test_registry_resolves_source_roots_before_global_and_project_default() -> None:
    registry = MCPRegistry(_make_settings())

    roots = registry.resolve_effective_roots(
        "local",
        project_root_uri="file:///project",
    )

    assert [item.uri for item in roots] == ["file:///source"]


def test_registry_falls_back_to_project_root_for_stdio_without_roots() -> None:
    settings = MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(command="uvx"),
        }
    )
    registry = MCPRegistry(settings)

    roots = registry.resolve_effective_roots(
        "local",
        project_root_uri="file:///project",
    )

    assert [item.uri for item in roots] == ["file:///project"]


def test_registry_lists_source_names_by_scope_and_transport() -> None:
    settings = MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(command="uvx"),
            "remote": MCPExternalSourceSettings(url="https://example.com/mcp"),
        }
    )
    registry = MCPRegistry(settings)

    assert registry.list_source_names() == ["local", "remote"]
    assert registry.list_source_names(scope="workflow") == ["local"]
    assert registry.list_source_names(scope="project") == ["remote"]
    assert registry.list_source_names(transport="stdio") == ["local"]
    assert registry.list_source_names(transport="http") == ["remote"]
