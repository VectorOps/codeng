from __future__ import annotations

import asyncio
import sys

import pytest
from aiohttp import web

from vocode.auth import ProjectCredentialManager
from vocode.mcp import client as mcp_client
from vocode.mcp.registry import MCPRegistry
from vocode.mcp import service as mcp_service
from vocode.mcp.service import MCPService
from vocode.mcp.service import MCPServiceError
from vocode.mcp import transports as mcp_transports
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


_SERVICE_LIST_CHANGED_SERVER = """
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
                'serverInfo': {'name': 'service-list-changed', 'version': '1.0.0'},
                'capabilities': {'tools': {'listChanged': True}}
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'notifications/initialized':
        initialized = True
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'method': 'notifications/tools/list_changed'
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'tools/list' and initialized:
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {
                'tools': [
                    {
                        'name': 'refreshed',
                        'description': 'Refreshed docs'
                    }
                ]
            }
        }) + '\\n')
        sys.stdout.flush()
"""


_SERVICE_ROOTS_SERVER = """
import json
import sys

request_id = 100
advertised_roots = False

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('method') == 'initialize':
        capabilities = msg.get('params', {}).get('capabilities', {})
        advertised_roots = 'roots' in capabilities
        sys.stderr.write(json.dumps({
            'kind': 'initialize',
            'capabilities': capabilities
        }) + '\\n')
        sys.stderr.flush()
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {
                'protocolVersion': '2025-03-26',
                'serverInfo': {'name': 'service-roots', 'version': '1.0.0'},
                'capabilities': {'roots': {'listChanged': True}}
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'notifications/initialized':
        if advertised_roots:
            sys.stdout.write(json.dumps({
                'jsonrpc': '2.0',
                'id': request_id,
                'method': 'roots/list',
                'params': {}
            }) + '\\n')
            sys.stdout.flush()
            request_id += 1
    elif msg.get('method') == 'notifications/roots/list_changed':
        sys.stderr.write(json.dumps({
            'kind': 'notification',
            'value': msg.get('method')
        }) + '\\n')
        sys.stderr.flush()
        if advertised_roots:
            sys.stdout.write(json.dumps({
                'jsonrpc': '2.0',
                'id': request_id,
                'method': 'roots/list',
                'params': {}
            }) + '\\n')
            sys.stdout.flush()
            request_id += 1
    elif 'id' in msg and 'result' in msg:
        sys.stderr.write(json.dumps({
            'kind': 'roots',
            'id': msg['id'],
            'value': msg.get('result', {}).get('roots', [])
        }) + '\\n')
        sys.stderr.flush()
"""


_SERVICE_DISCONNECTS_SERVER = """
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
                'serverInfo': {'name': 'disconnecting-service', 'version': '1.0.0'},
                'capabilities': {'tools': {'listChanged': False}}
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'notifications/initialized':
        break
"""


_SERVICE_PROMPTS_RESOURCES_SERVER = """
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
                'serverInfo': {'name': 'service-prompts-resources', 'version': '1.0.0'},
                'capabilities': {
                    'prompts': {},
                    'resources': {}
                }
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'notifications/initialized':
        initialized = True
    elif msg.get('method') == 'prompts/list' and initialized:
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {
                'prompts': [
                    {
                        'name': 'summarize',
                        'description': 'Summarize text',
                        'arguments': [
                            {'name': 'topic', 'required': True}
                        ]
                    },
                    {
                        'description': 'invalid prompt'
                    }
                ]
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'prompts/get' and initialized:
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {
                'messages': [
                    {
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': 'Prompt body'}
                        ]
                    }
                ]
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'resources/list' and initialized:
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {
                'resources': [
                    {
                        'uri': 'file:///docs/readme.md',
                        'name': 'readme',
                        'mimeType': 'text/markdown'
                    },
                    {
                        'name': 'broken-resource'
                    }
                ]
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'resources/read' and initialized:
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {
                'contents': [
                    {
                        'uri': msg.get('params', {}).get('uri'),
                        'mimeType': 'text/plain',
                        'text': 'Resource body'
                    }
                ]
            }
        }) + '\\n')
        sys.stdout.flush()
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


async def _wait_for_stderr_lines(
    session,
    count: int,
) -> list[str]:
    transport = session.transport
    assert isinstance(transport, type(session.transport))
    while len(transport.stderr_lines) < count:
        await asyncio.sleep(0.01)
    return transport.stderr_lines


async def _wait_for_restarted_session(
    service: MCPService,
    source_name: str,
    previous_session,
):
    while True:
        session = service.get_session(source_name)
        if session is not None and session is not previous_session:
            return session
        await asyncio.sleep(0.01)


async def _wait_for_session_close(session) -> None:
    while session.state.phase != "closed":
        await asyncio.sleep(0.01)


class _RecordedLogger:
    def __init__(self, records, context=None) -> None:
        self._records = records
        self._context = dict(context or {})

    def bind(self, **kwargs):
        merged = dict(self._context)
        merged.update(kwargs)
        return _RecordedLogger(self._records, merged)

    def info(self, event: str, **kwargs) -> None:
        self._records.append(("info", event, {**self._context, **kwargs}))

    def warning(self, event: str, **kwargs) -> None:
        self._records.append(("warning", event, {**self._context, **kwargs}))

    def exception(self, event: str, **kwargs) -> None:
        self._records.append(("exception", event, {**self._context, **kwargs}))


def _install_recorded_mcp_loggers(monkeypatch: pytest.MonkeyPatch):
    records = []
    recorded_logger = _RecordedLogger(records)
    monkeypatch.setattr(mcp_service, "logger", recorded_logger)
    monkeypatch.setattr(mcp_client, "logger", recorded_logger)
    monkeypatch.setattr(mcp_transports, "logger", recorded_logger)
    return records


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


@pytest.mark.asyncio
async def test_service_emits_mcp_lifecycle_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _install_recorded_mcp_loggers(monkeypatch)
    service = MCPService(_make_settings())

    await service.start_session("local")
    await service.refresh_tools("local")
    await service.close_all()

    events = [event for _, event, _ in records]

    assert "MCP session start requested" in events
    assert "MCP stdio transport started" in events
    assert "MCP session initialize succeeded" in events
    assert "MCP session started" in events
    assert "MCP tool refresh completed" in events
    assert "MCP session closing" in events
    assert "MCP session closed" in events

    refresh_record = next(
        record for record in records if record[1] == "MCP tool refresh completed"
    )
    assert refresh_record[2]["source_name"] == "local"
    assert refresh_record[2]["tool_count"] == 2
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


@pytest.mark.asyncio
async def test_service_ignores_stale_workflow_finish_for_different_run_id() -> None:
    settings = MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(
                command=sys.executable,
                args=["-c", _SERVICE_HANDSHAKE_SERVER],
            ),
        }
    )
    service = MCPService(settings)
    workflow = WorkflowConfig(
        mcp=MCPWorkflowSettings(
            tools=[MCPToolSelector(source="local", tool="*")],
        )
    )

    await service.start_workflow("wf", workflow, workflow_run_id="run-1")
    await service.start_workflow("wf", workflow, workflow_run_id="run-2")

    stale_finish = await service.finish_workflow(
        "wf",
        workflow_run_id="run-1",
    )

    assert stale_finish.started_sources == []
    assert stale_finish.stopped_sources == []
    assert set(service.list_sessions().keys()) == {"local"}

    current_finish = await service.finish_workflow(
        "wf",
        workflow_run_id="run-2",
    )

    assert current_finish.started_sources == []
    assert current_finish.stopped_sources == ["local"]
    assert service.list_sessions() == {}


def test_service_build_project_tools_materializes_helpers_and_filtered_adapters() -> (
    None
):
    settings = MCPSettings(
        discovery={"enabled": True},
        sources={
            "local": MCPStdioSourceSettings(
                command=sys.executable,
                args=["-c", _SERVICE_HANDSHAKE_SERVER],
            )
        },
    )
    service = MCPService(settings)
    workflow = WorkflowConfig(
        mcp=MCPWorkflowSettings(
            tools=[MCPToolSelector(source="local", tool="*")],
            disabled_tools=[MCPToolSelector(source="local", tool="search")],
        )
    )
    service._active_workflow = workflow
    service.cache_tool_descriptors(
        "local",
        [
            {"name": "search", "description": "Search docs"},
            {"name": "fetch", "description": "Fetch docs"},
        ],
    )

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
            self.source = type("_Source", (), {"scope": "workflow"})()

    class _Project:
        def __init__(self) -> None:
            self.mcp = service

    service._sessions["local"] = _PromptResourceSession()  # type: ignore[assignment]

    tools = service.build_project_tools(_Project(), {"mcp__local__blocked"})

    assert "mcp_discovery" in tools
    assert "mcp_get_prompt" in tools
    assert "mcp_read_resource" in tools
    assert "mcp__local__search" not in tools
    assert "mcp__local__fetch" in tools


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


def test_service_cache_tool_descriptors_skips_malformed_and_duplicate_names() -> None:
    service = MCPService(_make_settings())

    cached = service.cache_tool_descriptors(
        "local",
        [
            {
                "name": "search",
                "description": "Search docs",
            },
            {
                "name": "search",
                "description": "Duplicate search",
            },
            {
                "name": "Search",
                "description": "Case distinct",
            },
            {
                "name": "broken-schema",
                "inputSchema": {"type": "string"},
            },
            {
                "description": "missing name",
            },
        ],
    )

    assert set(cached.keys()) == {"Search"}
    assert cached["Search"].description == "Case distinct"


def test_service_cache_tool_descriptors_skips_normalized_internal_name_collisions() -> (
    None
):
    service = MCPService(_make_settings())

    cached = service.cache_tool_descriptors(
        "local.dev",
        [
            {
                "name": "search docs",
                "description": "Spaced name",
            },
            {
                "name": "search-docs",
                "description": "Dashed name",
            },
            {
                "name": "fetch",
                "description": "Unique tool",
            },
        ],
    )

    assert set(cached.keys()) == {"fetch"}
    assert cached["fetch"].description == "Unique tool"


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
async def test_service_logout_clears_only_target_source_token(
    tmp_path,
) -> None:
    credentials = ProjectCredentialManager(
        env={},
        credentials_path=tmp_path / "credentials.json",
    )
    settings = MCPSettings(
        sources={
            "remote_a": MCPExternalSourceSettings(
                url="https://example.com/mcp",
                auth=MCPAuthSettings(client_id="client-a", client_secret_env="A"),
            ),
            "remote_b": MCPExternalSourceSettings(
                url="https://example.com/mcp",
                auth=MCPAuthSettings(client_id="client-b", client_secret_env="B"),
            ),
        }
    )
    service = MCPService(settings, credentials=credentials)

    token_a = mcp_service.mcp_auth.MCPAuthToken(
        access_token="token-a",
        resource="https://example.com/mcp",
    )
    token_b = mcp_service.mcp_auth.MCPAuthToken(
        access_token="token-b",
        resource="https://example.com/mcp",
    )
    await service._auth._store_token("remote_a", token_a)
    await service._auth._store_token("remote_b", token_b)

    assert await service.authorization_status(
        "remote_a"
    ) == mcp_service.MCPAuthorizationStatus(
        source_name="remote_a",
        has_token=True,
        session_active=False,
    )
    assert await service.authorization_status(
        "remote_b"
    ) == mcp_service.MCPAuthorizationStatus(
        source_name="remote_b",
        has_token=True,
        session_active=False,
    )

    await service.logout("remote_a")

    assert await service.authorization_status(
        "remote_a"
    ) == mcp_service.MCPAuthorizationStatus(
        source_name="remote_a",
        has_token=False,
        session_active=False,
    )
    assert await service.authorization_status(
        "remote_b"
    ) == mcp_service.MCPAuthorizationStatus(
        source_name="remote_b",
        has_token=True,
        session_active=False,
    )


@pytest.mark.asyncio
async def test_service_lists_and_fetches_prompts_and_resources() -> None:
    settings = MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(
                command=sys.executable,
                args=["-c", _SERVICE_PROMPTS_RESOURCES_SERVER],
            )
        }
    )
    service = MCPService(settings)

    await service.start_session("local")
    prompts = await service.list_prompts("local")
    resources = await service.list_resources("local")
    prompt = await service.get_prompt("local", "summarize", {"topic": "build"})
    resource = await service.read_resource("local", "file:///docs/readme.md")

    assert service.list_prompt_sources() == ["local"]
    assert service.list_resource_sources() == ["local"]
    assert len(prompts) == 1
    assert prompts[0].prompt_name == "summarize"
    assert prompts[0].arguments[0].name == "topic"
    assert len(resources) == 1
    assert resources[0].uri == "file:///docs/readme.md"
    assert prompt["messages"][0]["content"][0]["text"] == "Prompt body"
    assert resource["contents"][0]["text"] == "Resource body"

    await service.close_all()


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


@pytest.mark.asyncio
async def test_service_refreshes_tools_after_list_changed_notification() -> None:
    settings = MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(
                command=sys.executable,
                args=["-c", _SERVICE_LIST_CHANGED_SERVER],
            )
        }
    )
    service = MCPService(settings)

    await service.start_session("local")

    async def _wait_for_cache() -> dict[str, object]:
        while True:
            cached = service.list_cached_tools("local")
            if "refreshed" in cached:
                return cached
            await asyncio.sleep(0.01)

    cached = await asyncio.wait_for(_wait_for_cache(), timeout=1.0)

    assert cached["refreshed"].description == "Refreshed docs"

    await service.close_all()


@pytest.mark.asyncio
async def test_service_recalculates_roots_for_active_project_scoped_session() -> None:
    settings = MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(
                command=sys.executable,
                args=["-c", _SERVICE_ROOTS_SERVER],
                scope="project",
            )
        }
    )
    service = MCPService(settings)

    session = await service.start_session("local")
    lines = await asyncio.wait_for(_wait_for_stderr_lines(session, 1), timeout=1.0)

    assert any('"capabilities": {}' in line for line in lines)

    await service.start_workflow(
        "wf",
        WorkflowConfig(
            mcp=MCPWorkflowSettings(
                roots=MCPRootSettings(
                    entries=[MCPRootEntry(uri="file:///workflow", name="workflow")]
                )
            )
        ),
    )
    restarted_session = await asyncio.wait_for(
        _wait_for_restarted_session(service, "local", session),
        timeout=1.0,
    )
    lines = await asyncio.wait_for(
        _wait_for_stderr_lines(restarted_session, 2),
        timeout=1.0,
    )

    assert any('"roots": {"listChanged": true}' in line for line in lines)
    assert any("file:///workflow" in line for line in lines)

    await service.finish_workflow("wf")
    final_session = await asyncio.wait_for(
        _wait_for_restarted_session(service, "local", restarted_session),
        timeout=1.0,
    )
    lines = await asyncio.wait_for(
        _wait_for_stderr_lines(final_session, 1), timeout=1.0
    )

    assert any('"capabilities": {}' in line for line in lines)

    await service.close_all()


@pytest.mark.asyncio
async def test_service_drops_disconnected_session_and_restarts_on_next_start() -> None:
    settings = MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(
                command=sys.executable,
                args=["-c", _SERVICE_DISCONNECTS_SERVER],
            )
        }
    )
    service = MCPService(settings)

    first_session = await service.start_session("local")
    await asyncio.wait_for(_wait_for_session_close(first_session), timeout=1.0)

    assert service.get_session("local") is None
    assert service.list_sessions() == {}

    second_session = await service.start_session("local")

    assert second_session is not first_session
    assert second_session.state.initialized is True

    await service.close_all()


@pytest.mark.asyncio
async def test_service_refresh_tools_rejects_disconnected_session() -> None:
    settings = MCPSettings(
        sources={
            "local": MCPStdioSourceSettings(
                command=sys.executable,
                args=["-c", _SERVICE_DISCONNECTS_SERVER],
            )
        }
    )
    service = MCPService(settings)

    session = await service.start_session("local")
    await asyncio.wait_for(_wait_for_session_close(session), timeout=1.0)

    with pytest.raises(MCPServiceError, match="no active session"):
        await service.refresh_tools("local")

    with pytest.raises(MCPServiceError, match="no active session"):
        await service.call_tool("local", "search", {})


@pytest.mark.asyncio
async def test_service_close_session_tolerates_session_close_failure() -> None:
    service = MCPService(_make_settings())

    session = await service.start_session("local")
    close_calls = {"count": 0}

    async def _failing_close() -> None:
        close_calls["count"] += 1
        session.state = session.state.model_copy(update={"phase": "closed"})
        raise RuntimeError("boom")

    session.close = _failing_close  # type: ignore[method-assign]

    await service.close_session("local")
    await service.close_session("local")

    assert close_calls["count"] == 1
    assert service.list_sessions() == {}


@pytest.mark.asyncio
async def test_service_close_all_tolerates_refresh_task_cancellation_failure() -> None:
    service = MCPService(_make_settings())

    await service.start_session("local")

    class _BrokenTask:
        def cancel(self) -> bool:
            raise RuntimeError("cancel failed")

        def __await__(self):
            if False:
                yield None
            return None

    service._tool_refresh_tasks["local"] = _BrokenTask()  # type: ignore[assignment]

    await service.close_all()

    assert service.list_sessions() == {}


@pytest.mark.asyncio
async def test_service_http_session_retries_on_insufficient_scope_challenge(
    tmp_path,
    unused_tcp_port,
) -> None:
    port = unused_tcp_port
    base_url = f"http://127.0.0.1:{port}"
    observed: dict[str, object] = {"token_scopes": [], "auth_headers": []}

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
        data = await request.post()
        scopes = data.get("scope")
        token_value = "base-token"
        if scopes == "tools.read tools.write":
            token_value = "step-up-token"
        observed["token_scopes"].append(scopes)
        return web.json_response(
            {
                "access_token": token_value,
                "token_type": "Bearer",
                "expires_in": 600,
                "scope": scopes,
            }
        )

    async def mcp_handler(request: web.Request) -> web.Response:
        authorization = request.headers.get("Authorization")
        observed["auth_headers"].append(authorization)
        payload = await request.json()
        if authorization == "Bearer base-token":
            return web.Response(
                status=403,
                headers={
                    "WWW-Authenticate": 'Bearer error="insufficient_scope", scope="tools.write"'
                },
                text="insufficient scope",
            )
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
                    scopes=["tools.read"],
                    max_step_up_attempts=2,
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
    assert observed["token_scopes"] == ["tools.read", "tools.read tools.write"]
    assert observed["auth_headers"] == [
        "Bearer base-token",
        "Bearer step-up-token",
        "Bearer step-up-token",
    ]

    await service.close_all()
    await runner.cleanup()
