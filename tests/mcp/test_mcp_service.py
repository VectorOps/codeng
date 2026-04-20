from __future__ import annotations

import sys

import pytest
from aiohttp import web

from vocode.connect_auth import ProjectCredentialManager
from vocode.mcp.registry import MCPRegistry
from vocode.mcp.service import MCPService
from vocode.mcp.service import MCPServiceError
from vocode.settings import MCPAuthSettings
from vocode.settings import MCPExternalSourceSettings
from vocode.settings import MCPProtocolSettings
from vocode.settings import MCPRootEntry
from vocode.settings import MCPRootSettings
from vocode.settings import MCPSettings
from vocode.settings import MCPStdioSourceSettings
from vocode.settings import MCPToolSelector
from vocode.settings import MCPWorkflowSettings
from vocode.settings import WorkflowConfig


_SERVICE_HANDSHAKE_SERVER = """
import json
import sys

initialized = False

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
        initialized = True
    elif msg.get('method') == 'tools/list' and initialized:
        if msg.get('params', {}).get('cursor') == 'cursor-2':
            sys.stdout.write(json.dumps({
                'jsonrpc': '2.0',
                'id': msg['id'],
                'result': {
                    'tools': [
                        {
                            'name': 'fetch',
                            'inputSchema': {
                                'type': 'object',
                                'properties': {'id': {'type': 'string'}}
                            }
                        }
                    ]
                }
            }) + '\\n')
        else:
            sys.stdout.write(json.dumps({
                'jsonrpc': '2.0',
                'id': msg['id'],
                'result': {
                    'tools': [
                        {
                            'name': 'search',
                            'description': 'Search docs'
                        }
                    ],
                    'nextCursor': 'cursor-2'
                }
            }) + '\\n')
        sys.stdout.flush()
"""


_BROKEN_SERVICE_SERVER = """
import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('method') == 'initialize':
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'method': 'notifications/ready'
        }) + '\\n')
        sys.stdout.flush()
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


@pytest.mark.asyncio
async def test_service_reconciles_workflow_scoped_sessions_differentially() -> None:
    settings = MCPSettings(
        sources={
            "local_a": MCPStdioSourceSettings(
                command=sys.executable,
                args=["-c", _SERVICE_HANDSHAKE_SERVER],
            ),
            "local_b": MCPStdioSourceSettings(
                command=sys.executable,
                args=["-c", _SERVICE_HANDSHAKE_SERVER],
            ),
        }
    )
    service = MCPService(settings)
    workflow_a = WorkflowConfig(
        mcp=MCPWorkflowSettings(
            tools=[MCPToolSelector(source="local_a", tool="*")],
        )
    )
    workflow_b = WorkflowConfig(
        mcp=MCPWorkflowSettings(
            tools=[MCPToolSelector(source="local_b", tool="*")],
        )
    )

    change_a = await service.start_workflow("wf-a", workflow_a)

    assert change_a.started_sources == ["local_a"]
    assert change_a.stopped_sources == []
    session_a = service.get_session("local_a")
    assert session_a is not None

    paused = await service.finish_workflow("wf-a", True)

    assert paused.started_sources == []
    assert paused.stopped_sources == []
    assert service.get_session("local_a") is session_a

    change_b = await service.start_workflow("wf-b", workflow_b)

    assert change_b.started_sources == ["local_b"]
    assert change_b.stopped_sources == ["local_a"]
    assert service.get_session("local_a") is None
    assert service.get_session("local_b") is not None

    finished = await service.finish_workflow("wf-b")

    assert finished.started_sources == []
    assert finished.stopped_sources == ["local_b"]
    assert service.list_sessions() == {}


def test_service_caches_and_clears_tool_descriptors_per_source() -> None:
    service = MCPService(_make_settings())

    cached = service.cache_tool_descriptors(
        "local",
        [
            {
                "name": "search",
                "description": "Search docs",
            },
            {
                "name": "fetch",
                "inputSchema": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                },
            },
        ],
    )

    assert set(cached.keys()) == {"search", "fetch"}
    assert service.list_cached_tools("local")["search"].description == "Search docs"
    assert (
        service.list_cached_tools("local")["fetch"].input_schema["properties"]["id"][
            "type"
        ]
        == "string"
    )

    service.clear_tool_cache("local")

    assert service.list_cached_tools("local") == {}


@pytest.mark.asyncio
async def test_service_refresh_tools_populates_cache_from_live_session() -> None:
    service = MCPService(_make_settings())

    await service.start_session("local")
    cached = await service.refresh_tools("local")

    assert set(cached.keys()) == {"search", "fetch"}
    assert cached["search"].description == "Search docs"
    assert cached["fetch"].input_schema["properties"]["id"]["type"] == "string"
    assert set(service.list_cached_tools("local").keys()) == {"search", "fetch"}

    await service.close_all()


@pytest.mark.asyncio
async def test_service_refresh_tools_requires_active_session() -> None:
    service = MCPService(_make_settings())

    with pytest.raises(MCPServiceError, match="no active session"):
        await service.refresh_tools("local")


@pytest.mark.asyncio
async def test_service_starts_external_http_session_with_auth(
    tmp_path,
    unused_tcp_port,
) -> None:
    port = unused_tcp_port
    base_url = f"http://127.0.0.1:{port}"
    observed: dict[str, object] = {"authorization": None, "token_requests": 0}

    async def protected_resource_handler(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "resource": f"{base_url}/mcp",
                "authorization_servers": [f"{base_url}/issuer"],
            }
        )

    async def authorization_server_handler(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "issuer": f"{base_url}/issuer",
                "token_endpoint": f"{base_url}/issuer/token",
            }
        )

    async def token_handler(request: web.Request) -> web.Response:
        observed["token_requests"] = int(observed["token_requests"]) + 1
        data = await request.post()
        assert data["client_id"] == "client-123"
        assert data["client_secret"] == "secret"
        return web.json_response(
            {
                "access_token": "http-token",
                "token_type": "Bearer",
                "expires_in": 600,
            }
        )

    async def mcp_handler(request: web.Request) -> web.Response:
        observed["authorization"] = request.headers.get("Authorization")
        payload = await request.json()
        if payload.get("method") == "initialize":
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "serverInfo": {
                            "name": "remote-server",
                            "version": "1.0.0",
                        },
                        "capabilities": {
                            "tools": {
                                "listChanged": False,
                            }
                        },
                    },
                }
            )
        return web.Response(status=204)

    app = web.Application()
    app.router.add_get(
        "/.well-known/oauth-protected-resource/mcp",
        protected_resource_handler,
    )
    app.router.add_get(
        "/issuer/.well-known/oauth-authorization-server",
        authorization_server_handler,
    )
    app.router.add_post("/issuer/token", token_handler)
    app.router.add_post("/mcp", mcp_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    settings = MCPSettings(
        sources={
            "remote": MCPExternalSourceSettings(
                url=f"{base_url}/mcp",
                auth=MCPAuthSettings(
                    mode="preregistered",
                    client_id="client-123",
                    client_secret_env="MCP_SECRET",
                ),
            )
        }
    )
    credentials = ProjectCredentialManager(
        env={"MCP_SECRET": "secret"},
        credentials_path=tmp_path / "credentials.json",
    )
    service = MCPService(settings, credentials=credentials)

    session = await service.start_session("remote")

    assert session.state.initialized is True
    assert observed["authorization"] == "Bearer http-token"
    assert observed["token_requests"] == 1

    await service.close_all()
    await runner.cleanup()


@pytest.mark.asyncio
async def test_service_does_not_retain_failed_session_start() -> None:
    settings = MCPSettings(
        sources={
            "broken": MCPStdioSourceSettings(
                command=sys.executable,
                args=["-c", _BROKEN_SERVICE_SERVER],
            )
        }
    )
    service = MCPService(settings)

    with pytest.raises(MCPServiceError, match="failed to start mcp source broken"):
        await service.start_session("broken")

    assert service.get_session("broken") is None
    assert service.list_sessions() == {}
