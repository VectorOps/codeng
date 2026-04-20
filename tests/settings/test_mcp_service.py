from __future__ import annotations

import sys

import pytest

from vocode.mcp.registry import MCPRegistry
from vocode.mcp.service import MCPService
from vocode.mcp.service import MCPServiceError
from vocode.settings import MCPExternalSourceSettings
from vocode.settings import MCPProtocolSettings
from vocode.settings import MCPRootEntry
from vocode.settings import MCPRootSettings
from vocode.settings import MCPSettings
from vocode.settings import MCPStdioSourceSettings


_SERVICE_HANDSHAKE_SERVER = """
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
                'serverInfo': {'name': 'service-server', 'version': '1.0.0'},
                'capabilities': {'tools': {'listChanged': False}}
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'notifications/initialized':
        break
"""


def _make_settings() -> MCPSettings:
    return MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(
                command=sys.executable,
                args=["-c", _SERVICE_HANDSHAKE_SERVER],
                roots=MCPRootSettings(
                    entries=[MCPRootEntry(uri="file:///tmp/project", name="project")]
                ),
            ),
            "remote": MCPExternalSourceSettings(
                url="https://example.com/mcp",
                headers={"X-Test": "yes"},
            ),
        },
        protocol=MCPProtocolSettings(
            request_timeout_s=12,
            max_request_timeout_s=30,
            startup_timeout_s=7,
            shutdown_timeout_s=5,
        ),
    )


def test_registry_builds_source_descriptors_from_settings() -> None:
    registry = MCPRegistry(_make_settings())

    sources = registry.list_sources()

    assert set(sources.keys()) == {"local", "remote"}
    assert sources["local"].transport == "stdio"
    assert sources["local"].scope == "workflow"
    assert sources["local"].request_timeout_s == 12
    assert sources["local"].roots[0].uri == "file:///tmp/project"
    assert sources["remote"].transport == "http"
    assert sources["remote"].scope == "project"


@pytest.mark.asyncio
async def test_service_starts_and_reuses_session_for_known_source() -> None:
    service = MCPService(_make_settings())

    session1 = await service.start_session("local")
    session2 = await service.start_session("local")

    assert session1 is session2
    assert session1.state.initialized is True
    assert session1.state.negotiation.server_info["name"] == "service-server"
    assert set(service.list_active_sources().keys()) == {"local"}
    assert service.list_active_sources()["local"].source_name == "local"
    assert service.get_negotiation("local") is not None
    assert service.get_negotiation("local").protocol_version == "2025-03-26"
    assert service.get_session_state("local") is not None
    assert service.get_session_state("local").initialized is True
    assert service.get_negotiation("missing") is None
    assert service.get_session_state("missing") is None

    await service.close_all()
    assert service.list_sessions() == {}
    assert service.list_active_sources() == {}


@pytest.mark.asyncio
async def test_service_rejects_unknown_source() -> None:
    service = MCPService(_make_settings())

    with pytest.raises(MCPServiceError, match="unknown mcp source"):
        await service.start_session("missing")


@pytest.mark.asyncio
async def test_service_rejects_when_mcp_disabled() -> None:
    settings = _make_settings()
    settings.enabled = False
    service = MCPService(settings)

    with pytest.raises(MCPServiceError, match="not enabled"):
        await service.start_session("local")


@pytest.mark.asyncio
async def test_service_start_and_finish_workflow_manage_workflow_scoped_sessions() -> (
    None
):
    service = MCPService(_make_settings())

    await service.start_workflow("wf")

    assert set(service.list_sessions().keys()) == {"local"}

    await service.finish_workflow("wf")

    assert service.list_sessions() == {}
