from __future__ import annotations

from pathlib import Path
import sys

import pytest

from vocode.mcp import naming as mcp_naming
from vocode.project import Project
from vocode.settings import MCPExternalSourceSettings
from vocode.settings import MCPSettings
from vocode.settings import MCPSourceScope
from vocode.settings import MCPStdioSourceSettings
from vocode.settings import Settings


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
async def test_project_finish_workflow_clears_current_workflow_without_mcp(tmp_path):
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=Settings(),
    )
    project.current_workflow = "wf"
    await project.on_workflow_finished("wf")

    assert project.current_workflow is None


@pytest.mark.asyncio
async def test_project_finish_workflow_does_not_clear_stale_current_workflow(tmp_path):
    project = Project(
        base_path=tmp_path,
        config_relpath=Path(".vocode/config-ng.yaml"),
        settings=Settings(),
    )
    project.current_workflow = "wf"
    await project.on_workflow_finished("other")

    assert project.current_workflow == "wf"


@pytest.mark.asyncio
async def test_project_does_not_materialize_cached_mcp_tools_globally(tmp_path):
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
    assert "mcp_discovery" not in project.tools
    assert "mcp_get_prompt" not in project.tools
    assert "mcp_read_resource" not in project.tools

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
