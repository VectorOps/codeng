from vocode.mcp.registry import MCPRegistry
from vocode.settings import MCPRootEntry
from vocode.settings import MCPRootSettings
from vocode.settings import MCPSettings
from vocode.settings import MCPExternalSourceSettings
from vocode.settings import MCPSettings
from vocode.settings import MCPStdioSourceSettings
from vocode.settings import MCPToolSelector
from vocode.settings import MCPWorkflowSettings
from vocode.settings import WorkflowConfig


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


def test_registry_resolves_effective_roots_with_workflow_precedence() -> None:
    registry = MCPRegistry(_make_settings())
    workflow = WorkflowConfig(
        mcp=MCPWorkflowSettings(
            roots=MCPRootSettings(
                entries=[MCPRootEntry(uri="file:///workflow", name="workflow")]
            )
        )
    )

    roots = registry.resolve_effective_roots(
        workflow,
        "local",
        project_root_uri="file:///project",
    )

    assert [item.uri for item in roots] == ["file:///workflow"]


def test_registry_resolves_source_roots_before_global_and_project_default() -> None:
    registry = MCPRegistry(_make_settings())

    roots = registry.resolve_effective_roots(
        None,
        "local",
        project_root_uri="file:///project",
    )

    assert [item.uri for item in roots] == ["file:///source"]


def test_registry_appends_workflow_roots_when_requested() -> None:
    registry = MCPRegistry(_make_settings())
    workflow = WorkflowConfig(
        mcp=MCPWorkflowSettings(
            roots=MCPRootSettings(
                merge_mode="append",
                entries=[MCPRootEntry(uri="file:///workflow", name="workflow")],
            )
        )
    )

    roots = registry.resolve_effective_roots(
        workflow,
        "local",
        project_root_uri="file:///project",
    )

    assert [item.uri for item in roots] == ["file:///source", "file:///workflow"]


def test_registry_falls_back_to_project_root_for_stdio_without_roots() -> None:
    settings = MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(command="uvx"),
        }
    )
    registry = MCPRegistry(settings)

    roots = registry.resolve_effective_roots(
        None,
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


def test_registry_resolves_workflow_source_descriptors_from_selectors() -> None:
    settings = MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(command="uvx"),
            "local_b": MCPStdioSourceSettings(command="uvx"),
            "remote": MCPExternalSourceSettings(url="https://example.com/mcp"),
        }
    )
    registry = MCPRegistry(settings)
    workflow = WorkflowConfig(
        mcp=MCPWorkflowSettings(
            tools=[MCPToolSelector(source="local_b", tool="*")],
        )
    )

    sources = registry.resolve_workflow_sources(workflow)

    assert list(sources.keys()) == ["local_b"]
    assert sources["local_b"].scope == "workflow"


def test_registry_resolves_all_workflow_sources_without_workflow_context() -> None:
    settings = MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(command="uvx"),
            "remote": MCPExternalSourceSettings(url="https://example.com/mcp"),
        }
    )
    registry = MCPRegistry(settings)

    sources = registry.resolve_workflow_sources(None)

    assert list(sources.keys()) == ["local"]


def test_registry_resolves_workflow_tools_with_allow_and_deny_selectors() -> None:
    registry = MCPRegistry(_make_settings())
    workflow = WorkflowConfig(
        mcp=MCPWorkflowSettings(
            tools=[MCPToolSelector(source="local", tool="*")],
            disabled_tools=[MCPToolSelector(source="local", tool="search")],
        )
    )

    tools = registry.resolve_workflow_tools(
        workflow,
        "local",
        ["search", "fetch"],
    )

    assert tools == ["fetch"]


def test_registry_returns_no_tools_when_workflow_mcp_is_missing_or_disabled() -> None:
    registry = MCPRegistry(_make_settings())

    assert registry.resolve_workflow_tools(None, "local", ["search"]) == []
    assert (
        registry.resolve_workflow_tools(
            WorkflowConfig(mcp=MCPWorkflowSettings(enabled=False)),
            "local",
            ["search"],
        )
        == []
    )


def test_registry_reports_if_workflow_tool_is_enabled() -> None:
    registry = MCPRegistry(_make_settings())
    workflow = WorkflowConfig(
        mcp=MCPWorkflowSettings(
            tools=[MCPToolSelector(source="local", tool="*")],
            disabled_tools=[MCPToolSelector(source="local", tool="search")],
        )
    )

    assert registry.is_workflow_tool_enabled(workflow, "local", "fetch") is True
    assert registry.is_workflow_tool_enabled(workflow, "local", "search") is False
    assert registry.is_workflow_tool_enabled(None, "local", "fetch") is False
