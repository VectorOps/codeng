import pytest

from vocode import settings as vocode_settings
from vocode.mcp import naming as mcp_naming
from vocode.mcp.service import MCPService
from vocode.state import WorkflowExecution
from vocode.tools.base import ToolReq
from vocode.tools.mcp_discovery_tool import MCPDiscoveryTool
from vocode.tools.mcp_tool import MCPToolAdapter


class _ProjectStub:
    def __init__(self, settings: vocode_settings.Settings | None = None) -> None:
        self.settings = settings or vocode_settings.Settings()
        self.current_workflow = None
        self.tools = {}
        self.mcp = None


@pytest.mark.asyncio
async def test_mcp_discovery_tool_lists_hidden_and_visible_tools() -> None:
    settings = vocode_settings.Settings(
        workflows={
            "wf": vocode_settings.WorkflowConfig(
                mcp=vocode_settings.MCPWorkflowSettings(
                    tools=[vocode_settings.MCPToolSelector(source="local", tool="*")],
                )
            )
        },
        mcp=vocode_settings.MCPSettings(
            sources={
                "local": vocode_settings.MCPStdioSourceSettings(
                    command="uvx",
                    scope=vocode_settings.MCPSourceScope.project,
                )
            }
        ),
    )
    project = _ProjectStub(settings=settings)
    project.current_workflow = "wf"
    project.mcp = MCPService(settings.mcp)
    project.mcp.cache_tool_descriptors(
        "local",
        [
            {"name": "search docs", "description": "Search docs"},
            {"name": "fetch", "description": "Fetch docs"},
        ],
    )
    descriptor = project.mcp.list_cached_tools("local")["fetch"]
    fetch_name = mcp_naming.build_internal_tool_name("local", "fetch")
    search_name = mcp_naming.build_internal_tool_name("local", "search docs")
    project.tools[fetch_name] = MCPToolAdapter(
        project,
        descriptor,
        fetch_name,
    )
    tool = MCPDiscoveryTool(project)

    result = await tool.run(
        ToolReq(
            execution=WorkflowExecution(workflow_name="wf"),
            spec=vocode_settings.ToolSpec(name="mcp_discovery"),
        ),
        {},
    )

    assert result.data == {
        "tools": [
            {
                "name": fetch_name,
                "source": "local",
                "tool": "fetch",
                "title": None,
                "description": "Fetch docs",
                "hidden": False,
                "score": 0.0,
                "tool_spec": {
                    "type": "function",
                    "function": {
                        "name": fetch_name,
                        "description": "Fetch docs",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                },
            },
            {
                "name": search_name,
                "source": "local",
                "tool": "search docs",
                "title": None,
                "description": "Search docs",
                "hidden": True,
                "score": 0.0,
                "tool_spec": {
                    "type": "function",
                    "function": {
                        "name": search_name,
                        "description": "Search docs",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                },
            },
        ]
    }


@pytest.mark.asyncio
async def test_mcp_discovery_tool_filters_by_source_and_workflow_selection() -> None:
    settings = vocode_settings.Settings(
        workflows={
            "wf": vocode_settings.WorkflowConfig(
                mcp=vocode_settings.MCPWorkflowSettings(
                    tools=[
                        vocode_settings.MCPToolSelector(
                            source="local",
                            tool="search docs",
                        )
                    ],
                )
            )
        },
        mcp=vocode_settings.MCPSettings(
            sources={
                "local": vocode_settings.MCPStdioSourceSettings(
                    command="uvx",
                    scope=vocode_settings.MCPSourceScope.project,
                ),
                "other": vocode_settings.MCPStdioSourceSettings(
                    command="uvx",
                    scope=vocode_settings.MCPSourceScope.project,
                ),
            }
        ),
    )
    project = _ProjectStub(settings=settings)
    project.current_workflow = "wf"
    project.mcp = MCPService(settings.mcp)
    project.mcp.cache_tool_descriptors(
        "local",
        [
            {"name": "search docs", "description": "Search docs"},
            {"name": "fetch", "description": "Fetch docs"},
        ],
    )
    project.mcp.cache_tool_descriptors(
        "other",
        [{"name": "other_tool", "description": "Other docs"}],
    )
    tool = MCPDiscoveryTool(project)

    result = await tool.run(
        ToolReq(
            execution=WorkflowExecution(workflow_name="wf"),
            spec=vocode_settings.ToolSpec(name="mcp_discovery"),
        ),
        {"source": "local"},
    )

    assert result.data == {
        "tools": [
            {
                "name": mcp_naming.build_internal_tool_name("local", "search docs"),
                "source": "local",
                "tool": "search docs",
                "title": None,
                "description": "Search docs",
                "hidden": True,
                "score": 0.0,
                "tool_spec": {
                    "type": "function",
                    "function": {
                        "name": mcp_naming.build_internal_tool_name(
                            "local", "search docs"
                        ),
                        "description": "Search docs",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                },
            }
        ]
    }


@pytest.mark.asyncio
async def test_mcp_discovery_tool_ranks_query_matches_and_limits_results() -> None:
    settings = vocode_settings.Settings(
        workflows={
            "wf": vocode_settings.WorkflowConfig(
                mcp=vocode_settings.MCPWorkflowSettings(
                    tools=[vocode_settings.MCPToolSelector(source="local", tool="*")],
                )
            )
        },
        mcp=vocode_settings.MCPSettings(
            discovery=vocode_settings.MCPDiscoverySettings(max_results=1),
            sources={
                "local": vocode_settings.MCPStdioSourceSettings(
                    command="uvx",
                    scope=vocode_settings.MCPSourceScope.project,
                )
            },
        ),
    )
    project = _ProjectStub(settings=settings)
    project.current_workflow = "wf"
    project.mcp = MCPService(settings.mcp)
    project.mcp.cache_tool_descriptors(
        "local",
        [
            {
                "name": "search_code",
                "description": "Search project code and symbols",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            },
            {
                "name": "fetch_file",
                "description": "Fetch a file by path",
            },
        ],
    )
    tool = MCPDiscoveryTool(project)

    result = await tool.run(
        ToolReq(
            execution=WorkflowExecution(workflow_name="wf"),
            spec=vocode_settings.ToolSpec(name="mcp_discovery"),
        ),
        {"query": "search code symbols"},
    )

    assert result.data is not None
    tools = result.data["tools"]
    assert len(tools) == 1
    assert tools[0]["tool"] == "search_code"
    assert tools[0]["score"] > 0.0


@pytest.mark.asyncio
async def test_mcp_discovery_tool_openapi_spec() -> None:
    tool = MCPDiscoveryTool(_ProjectStub())

    spec = await tool.openapi_spec(vocode_settings.ToolSpec(name="mcp_discovery"))

    assert spec["name"] == "mcp_discovery"
    assert "source" in spec["parameters"]["properties"]
    assert "query" in spec["parameters"]["properties"]
    assert "max_results" in spec["parameters"]["properties"]
