from __future__ import annotations

from pathlib import Path
import sys

import pytest

from vocode import state
from vocode.mcp import naming as mcp_naming
from vocode.project import Project
from vocode.tools.base import ToolReq
from vocode.settings import MCPExternalSourceSettings
from vocode.settings import MCPSourceScope
from vocode.settings import MCPSettings
from vocode.settings import MCPStdioSourceSettings
from vocode.settings import MCPToolSelector
from vocode.settings import MCPWorkflowSettings
from vocode.settings import ToolSpec
from vocode.settings import Settings
from vocode.settings import WorkflowConfig


class _DummyKnowPM:
    def get_enabled_tools(self):
        return []


class _DummyKnowProject:
    def __init__(self):
        self.pm = _DummyKnowPM()

    async def start(self, settings):
        return None

    async def shutdown(self):
        return None

    async def refresh_all(self):
        return None


_PROJECT_MCP_SERVER = """
import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('method') == 'initialize':
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {
                'protocolVersion': '2025-03-26',
                'serverInfo': {'name': 'project-mcp', 'version': '1.0.0'},
                'capabilities': {'tools': {'listChanged': False}}
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'notifications/initialized':
        break
"""


@pytest.mark.asyncio
async def test_project_start_initializes_subsystems_and_tools(tmp_path):
    settings = Settings()
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()

    await project.start()

    assert project.processes is not None
    assert project.shells is not None
    assert isinstance(project.tools, dict)
    assert "exec" in project.tools

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_refreshes_materialized_mcp_tools_when_cache_updates(tmp_path):
    settings = Settings(
        workflows={
            "wf": WorkflowConfig(
                mcp=MCPWorkflowSettings(
                    tools=[MCPToolSelector(source="local", tool="*")],
                )
            )
        },
        mcp=MCPSettings(
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            }
        ),
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()
    project.current_workflow = "wf"

    await project.start()
    assert project.mcp is not None

    tool_name = mcp_naming.build_internal_tool_name("local", "search")
    assert tool_name not in project.tools

    project.mcp.cache_tool_descriptors(
        "local",
        [{"name": "search", "description": "Search docs"}],
    )

    assert tool_name in project.tools

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_finish_workflow_clears_current_workflow_without_mcp(tmp_path):
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=Settings(),
    )
    project.current_workflow = "wf"
    project.current_workflow_run_id = "run-1"

    await project.on_workflow_finished("wf", workflow_run_id="run-1")

    assert project.current_workflow is None
    assert project.current_workflow_run_id is None


@pytest.mark.asyncio
async def test_project_finish_workflow_does_not_clear_stale_current_workflow(tmp_path):
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=Settings(),
    )
    project.current_workflow = "wf"
    project.current_workflow_run_id = "run-2"

    await project.on_workflow_finished("wf", workflow_run_id="run-1")

    assert project.current_workflow == "wf"
    assert project.current_workflow_run_id == "run-2"


@pytest.mark.asyncio
async def test_project_finish_workflow_does_not_clear_different_workflow_without_run_id(
    tmp_path,
):
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=Settings(),
    )
    project.current_workflow = "wf-a"
    project.current_workflow_run_id = None

    await project.on_workflow_finished("wf-b")

    assert project.current_workflow == "wf-a"
    assert project.current_workflow_run_id is None


@pytest.mark.asyncio
async def test_project_materializes_prompt_and_resource_helper_tools(tmp_path):
    settings = Settings(
        tools=[
            ToolSpec(name="mcp_get_prompt"),
            ToolSpec(name="mcp_read_resource"),
        ],
        workflows={
            "wf": WorkflowConfig(
                mcp=MCPWorkflowSettings(
                    tools=[MCPToolSelector(source="local", tool="*")],
                )
            )
        },
        mcp=MCPSettings(
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            }
        ),
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()
    project.current_workflow = "wf"

    await project.start()
    assert project.mcp is not None

    class _PromptResourceSession:
        def __init__(self) -> None:
            self.state = type(
                "_State",
                (),
                {
                    "initialized": True,
                    "phase": "operating",
                    "negotiation": type(
                        "_Negotiation",
                        (),
                        {
                            "server_capabilities": type(
                                "_Capabilities",
                                (),
                                {"prompts": True, "resources": True},
                            )()
                        },
                    )(),
                },
            )()

    project.mcp._sessions["local"] = _PromptResourceSession()  # type: ignore[assignment]
    project.refresh_tools_from_registry()

    assert "mcp_get_prompt" in project.tools
    assert "mcp_read_resource" in project.tools

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_refresh_tools_merges_cached_mcp_tools(tmp_path):
    settings = Settings(
        workflows={
            "wf": WorkflowConfig(
                mcp=MCPWorkflowSettings(
                    tools=[MCPToolSelector(source="local", tool="*")],
                )
            )
        },
        mcp=MCPSettings(
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            }
        ),
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()
    project.current_workflow = "wf"

    await project.start()
    assert project.mcp is not None
    project.mcp.cache_tool_descriptors(
        "local",
        [
            {
                "name": "search",
                "description": "Search docs",
                "inputSchema": {
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                },
            }
        ],
    )

    project.refresh_tools_from_registry()

    tool_name = mcp_naming.build_internal_tool_name("local", "search")
    assert tool_name in project.tools
    adapter = project.tools[tool_name]
    spec = await adapter.openapi_spec(ToolSpec(name=tool_name))
    assert spec["name"] == tool_name
    assert spec["parameters"]["properties"]["q"]["type"] == "string"

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_mcp_tool_adapter_invokes_service_tool_call(tmp_path):
    settings = Settings(
        workflows={
            "wf": WorkflowConfig(
                mcp=MCPWorkflowSettings(
                    tools=[MCPToolSelector(source="local", tool="*")],
                )
            )
        },
        mcp=MCPSettings(
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            }
        ),
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()
    project.current_workflow = "wf"

    await project.start()
    assert project.mcp is not None

    async def _call_tool(source_name: str, tool_name: str, arguments):
        assert source_name == "local"
        assert tool_name == "search"
        assert arguments == {"q": "test"}
        return {
            "content": [
                {"type": "text", "text": "hello"},
                {"type": "text", "text": "world"},
            ]
        }

    project.mcp.call_tool = _call_tool  # type: ignore[method-assign]
    project.mcp.cache_tool_descriptors(
        "local",
        [{"name": "search", "description": "Search docs"}],
    )
    project.refresh_tools_from_registry()

    tool_name = mcp_naming.build_internal_tool_name("local", "search")
    adapter = project.tools[tool_name]
    result = await adapter.run(
        ToolReq(
            execution=state.WorkflowExecution(workflow_name="wf"),
            spec=ToolSpec(name=tool_name),
        ),
        {"q": "test"},
    )

    assert result is not None
    assert result.text == "hello\nworld"
    assert result.data is not None
    assert result.data["content"][0]["text"] == "hello"
    assert result.is_error is False

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_mcp_tool_adapter_preserves_remote_execution_error_payload(
    tmp_path,
):
    settings = Settings(
        workflows={
            "wf": WorkflowConfig(
                mcp=MCPWorkflowSettings(
                    tools=[MCPToolSelector(source="local", tool="*")],
                )
            )
        },
        mcp=MCPSettings(
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            }
        ),
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()
    project.current_workflow = "wf"

    await project.start()
    assert project.mcp is not None

    async def _call_tool(source_name: str, tool_name: str, arguments):
        assert source_name == "local"
        assert tool_name == "search"
        assert arguments == {"q": "test"}
        return {
            "content": [
                {"type": "text", "text": "remote failure"},
            ],
            "isError": True,
            "structuredContent": {"code": "REMOTE_FAILURE"},
        }

    project.mcp.call_tool = _call_tool  # type: ignore[method-assign]
    project.mcp.cache_tool_descriptors(
        "local",
        [{"name": "search", "description": "Search docs"}],
    )
    project.refresh_tools_from_registry()

    tool_name = mcp_naming.build_internal_tool_name("local", "search")
    adapter = project.tools[tool_name]
    result = await adapter.run(
        ToolReq(
            execution=state.WorkflowExecution(workflow_name="wf"),
            spec=ToolSpec(name=tool_name),
        ),
        {"q": "test"},
    )

    assert result is not None
    assert result.text == "remote failure"
    assert result.is_error is True
    assert result.data is not None
    assert result.data["isError"] is True
    assert result.data["structuredContent"]["code"] == "REMOTE_FAILURE"

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_does_not_materialize_mcp_tools_without_workflow_selection(
    tmp_path,
):
    settings = Settings(
        mcp=MCPSettings(
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            }
        )
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()

    await project.start()
    assert project.mcp is not None
    project.mcp.cache_tool_descriptors(
        "local",
        [{"name": "search", "description": "Search docs"}],
    )

    project.refresh_tools_from_registry()

    tool_name = mcp_naming.build_internal_tool_name("local", "search")
    assert tool_name not in project.tools

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_mcp_disabled_tools_override_allow_selectors(tmp_path):
    settings = Settings(
        workflows={
            "wf": WorkflowConfig(
                mcp=MCPWorkflowSettings(
                    tools=[MCPToolSelector(source="local", tool="*")],
                    disabled_tools=[
                        MCPToolSelector(source="local", tool="search"),
                    ],
                )
            )
        },
        mcp=MCPSettings(
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            }
        ),
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()
    project.current_workflow = "wf"

    await project.start()
    assert project.mcp is not None
    project.mcp.cache_tool_descriptors(
        "local",
        [
            {"name": "search", "description": "Search docs"},
            {"name": "fetch", "description": "Fetch docs"},
        ],
    )

    project.refresh_tools_from_registry()

    search_tool_name = mcp_naming.build_internal_tool_name("local", "search")
    fetch_tool_name = mcp_naming.build_internal_tool_name("local", "fetch")
    assert search_tool_name not in project.tools
    assert fetch_tool_name in project.tools

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_hide_listed_mcp_tools_keeps_discovery_tool(tmp_path):
    settings = Settings(
        workflows={
            "wf": WorkflowConfig(
                mcp=MCPWorkflowSettings(
                    hide_listed_tools=True,
                    tools=[MCPToolSelector(source="local", tool="*")],
                )
            )
        },
        mcp=MCPSettings(
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            }
        ),
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()
    project.current_workflow = "wf"

    await project.start()
    assert project.mcp is not None
    project.mcp.cache_tool_descriptors(
        "local",
        [{"name": "search", "description": "Search docs"}],
    )

    project.refresh_tools_from_registry()

    tool_name = mcp_naming.build_internal_tool_name("local", "search")
    assert tool_name not in project.tools
    assert "mcp_discovery" in project.tools

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_hide_listed_mcp_tools_respects_disabled_discovery_tool(
    tmp_path,
):
    settings = Settings(
        workflows={
            "wf": WorkflowConfig(
                mcp=MCPWorkflowSettings(
                    hide_listed_tools=True,
                    tools=[MCPToolSelector(source="local", tool="*")],
                )
            )
        },
        mcp=MCPSettings(
            discovery={"enabled": False},
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            },
        ),
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()
    project.current_workflow = "wf"

    await project.start()
    assert project.mcp is not None
    project.mcp.cache_tool_descriptors(
        "local",
        [{"name": "search", "description": "Search docs"}],
    )

    project.refresh_tools_from_registry()

    tool_name = mcp_naming.build_internal_tool_name("local", "search")
    assert tool_name not in project.tools
    assert "mcp_discovery" not in project.tools

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_global_hide_listed_mcp_tools_applies_without_workflow_override(
    tmp_path,
):
    settings = Settings(
        workflows={
            "wf": WorkflowConfig(
                mcp=MCPWorkflowSettings(
                    tools=[MCPToolSelector(source="local", tool="*")],
                )
            )
        },
        mcp=MCPSettings(
            hide_listed_tools=True,
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            },
        ),
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()
    project.current_workflow = "wf"

    await project.start()
    assert project.mcp is not None
    project.mcp.cache_tool_descriptors(
        "local",
        [{"name": "search", "description": "Search docs"}],
    )

    project.refresh_tools_from_registry()

    tool_name = mcp_naming.build_internal_tool_name("local", "search")
    assert tool_name not in project.tools
    assert "mcp_discovery" in project.tools

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_global_hide_listed_mcp_tools_cannot_be_disabled_by_workflow(
    tmp_path,
):
    settings = Settings(
        workflows={
            "wf": WorkflowConfig(
                mcp=MCPWorkflowSettings(
                    hide_listed_tools=False,
                    tools=[MCPToolSelector(source="local", tool="*")],
                )
            )
        },
        mcp=MCPSettings(
            hide_listed_tools=True,
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            },
        ),
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()
    project.current_workflow = "wf"

    await project.start()
    assert project.mcp is not None
    project.mcp.cache_tool_descriptors(
        "local",
        [{"name": "search", "description": "Search docs"}],
    )

    project.refresh_tools_from_registry()

    tool_name = mcp_naming.build_internal_tool_name("local", "search")
    assert tool_name not in project.tools
    assert "mcp_discovery" in project.tools

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_start_initializes_project_scoped_mcp_service(tmp_path):
    settings = Settings(
        mcp=MCPSettings(
            sources={
                "local": MCPStdioSourceSettings(
                    command=sys.executable,
                    args=["-c", _PROJECT_MCP_SERVER],
                    scope=MCPSourceScope.project,
                ),
            }
        )
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()

    await project.start()

    assert project.mcp is not None
    assert set(project.mcp.list_sessions().keys()) == {"local"}

    await project.shutdown()
    assert project.mcp is not None
    assert project.mcp.list_sessions() == {}


@pytest.mark.asyncio
async def test_project_start_creates_disabled_mcp_service_without_sessions(tmp_path):
    settings = Settings(
        mcp=MCPSettings(
            enabled=False,
            sources={
                "remote": MCPExternalSourceSettings(url="https://example.com/mcp"),
            },
        )
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()

    await project.start()

    assert project.mcp is not None
    assert project.mcp.list_sessions() == {}

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_start_does_not_eagerly_start_external_mcp_sources(tmp_path):
    settings = Settings(
        mcp=MCPSettings(
            sources={
                "remote": MCPExternalSourceSettings(url="https://example.com/mcp"),
            }
        )
    )
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )
    project.know = _DummyKnowProject()

    await project.start()

    assert project.mcp is not None
    assert project.mcp.list_sessions() == {}

    await project.shutdown()


@pytest.mark.asyncio
async def test_project_start_with_know_disabled(tmp_path):
    settings = Settings()
    settings.know_enabled = False
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=settings,
    )

    project.know = _DummyKnowProject()

    await project.start()

    assert project.processes is not None
    assert project.shells is not None
    assert isinstance(project.tools, dict)
    assert "exec" in project.tools

    await project.shutdown()
