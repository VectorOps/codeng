from __future__ import annotations

import asyncio
import sys

import pytest
from aiohttp import web

from vocode.mcp.client import MCPClientError
from vocode.mcp.client import MCPClientSession
from vocode.mcp.models import MCPClientCapabilities
from vocode.mcp.models import MCPRootDescriptor
from vocode.mcp.models import MCPSourceDescriptor
from vocode.mcp.models import MCPTransportKind
from vocode.mcp.transports import MCPHTTPTransport
from vocode.mcp.transports import MCPStdioTransport
from vocode.mcp.transports import MCPTransportError


_HANDSHAKE_SERVER = """
import json
import sys

initialized = False
tools_list_calls = 0

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('method') == 'initialize':
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {
                'protocolVersion': '2025-03-26',
                'serverInfo': {'name': 'mcp-test-server', 'version': '1.2.3'},
                'capabilities': {
                    'tools': {'listChanged': True},
                    'roots': {'listChanged': True}
                }
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'notifications/initialized':
        initialized = True
    elif msg.get('method') == 'tools/list' and initialized:
        tools_list_calls += 1
        if msg.get('params', {}).get('cursor') == 'cursor-2':
            sys.stdout.write(json.dumps({
                'jsonrpc': '2.0',
                'id': msg['id'],
                'result': {
                    'tools': [
                        {'name': 'final'}
                    ]
                }
            }) + '\\n')
        else:
            sys.stdout.write(json.dumps({
                'jsonrpc': '2.0',
                'id': msg['id'],
                'result': {
                    'tools': [
                        {'name': 'search'},
                        {'name': 'fetch'}
                    ],
                    'nextCursor': 'cursor-2'
                }
            }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'tools/call' and initialized:
        if msg.get('params', {}).get('name') == 'explode':
            sys.stdout.write(json.dumps({
                'jsonrpc': '2.0',
                'id': msg['id'],
                'error': {
                    'code': -32001,
                    'message': 'tool failed'
                }
            }) + '\\n')
        else:
            sys.stdout.write(json.dumps({
                'jsonrpc': '2.0',
                'id': msg['id'],
                'result': {
                    'content': [
                        {'type': 'text', 'text': 'ok'}
                    ],
                    'isError': False,
                    'structuredContent': {'echo': msg.get('params', {}).get('arguments', {})}
                }
            }) + '\\n')
        sys.stdout.flush()
"""


_BAD_HANDSHAKE_SERVER = """
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


_NO_TOOLS_SERVER = """
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
                'serverInfo': {'name': 'mcp-no-tools', 'version': '1.0.0'},
                'capabilities': {
                    'roots': {'listChanged': False}
                }
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'notifications/initialized':
        break
"""


_TIMEOUT_SERVER = """
import json
import sys
import time

cancelled = []

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('method') == 'initialize':
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {
                'protocolVersion': '2025-03-26',
                'serverInfo': {'name': 'timeout-server', 'version': '1.0.0'},
                'capabilities': {
                    'tools': {'listChanged': False}
                }
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'notifications/initialized':
        pass
    elif msg.get('method') == 'slow/method':
        time.sleep(0.05)
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {'done': True}
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'notifications/cancelled':
        cancelled.append(msg.get('params'))
        sys.stderr.write(json.dumps(cancelled) + '\\n')
        sys.stderr.flush()
"""


_LIST_CHANGED_SERVER = """
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
                'serverInfo': {'name': 'list-changed-server', 'version': '1.0.0'},
                'capabilities': {
                    'tools': {'listChanged': True}
                }
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
                    {'name': 'refreshed'}
                ]
            }
        }) + '\\n')
        sys.stdout.flush()
"""


_ROOTS_SERVER = """
import json
import sys

request_id = 100

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('method') == 'initialize':
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {
                'protocolVersion': '2025-03-26',
                'serverInfo': {'name': 'roots-server', 'version': '1.0.0'},
                'capabilities': {
                    'roots': {'listChanged': True}
                }
            }
        }) + '\\n')
        sys.stdout.flush()
    elif msg.get('method') == 'notifications/initialized':
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


_PROMPTS_RESOURCES_SERVER = """
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
                'serverInfo': {'name': 'prompt-resource-server', 'version': '1.0.0'},
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


def _make_source() -> MCPSourceDescriptor:
    return MCPSourceDescriptor(
        source_name="local",
        transport=MCPTransportKind.stdio,
        scope="workflow",
        startup_timeout_s=15,
        shutdown_timeout_s=10,
        request_timeout_s=30,
    )


async def _wait_for_stderr_lines(
    transport: MCPStdioTransport,
    count: int,
) -> list[str]:
    while len(transport.stderr_lines) < count:
        await asyncio.sleep(0.01)
    return transport.stderr_lines


def _make_http_source() -> MCPSourceDescriptor:
    return MCPSourceDescriptor(
        source_name="remote",
        transport=MCPTransportKind.http,
        scope="project",
        startup_timeout_s=15,
        shutdown_timeout_s=10,
        request_timeout_s=30,
    )


@pytest.mark.asyncio
async def test_client_session_initialize_populates_negotiated_state() -> None:
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _HANDSHAKE_SERVER]),
        client_capabilities=MCPClientCapabilities(roots=True, roots_list_changed=True),
    )

    result = await session.initialize()

    assert result["protocolVersion"] == "2025-03-26"
    assert session.state.initialized is True
    assert session.state.phase == "operating"
    assert session.state.negotiation.protocol_version == "2025-03-26"
    assert session.state.negotiation.server_info["name"] == "mcp-test-server"
    assert session.state.negotiation.client_capabilities.roots is True
    assert session.state.negotiation.server_capabilities.tools is True
    assert session.state.negotiation.server_capabilities.tools_list_changed is True
    assert session.state.negotiation.server_capabilities.roots is True
    assert session.state.negotiation.server_capabilities.roots_list_changed is True

    await session.close()


@pytest.mark.asyncio
async def test_client_session_start_runs_transport_and_initialize() -> None:
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _HANDSHAKE_SERVER]),
    )

    await session.start()

    assert session.transport.is_running is True
    assert session.state.initialized is True

    await session.close()
    assert session.state.phase == "closed"


@pytest.mark.asyncio
async def test_client_session_rejects_unexpected_notification_during_initialize() -> (
    None
):
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _BAD_HANDSHAKE_SERVER]),
    )

    with pytest.raises(MCPClientError, match="unexpected notification"):
        await session.initialize()

    assert session.state.phase == "closed"
    assert session.state.initialized is False
    assert session.state.negotiation.protocol_version is None
    assert session.state.last_error is not None


@pytest.mark.asyncio
async def test_client_session_list_tools_after_initialize() -> None:
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _HANDSHAKE_SERVER]),
    )

    await session.start()
    result = await session.list_tools()

    assert [item["name"] for item in result["tools"]] == ["search", "fetch"]
    assert result["nextCursor"] == "cursor-2"

    await session.close()


@pytest.mark.asyncio
async def test_client_session_rejects_tools_list_when_capability_missing() -> None:
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _NO_TOOLS_SERVER]),
    )

    await session.start()

    with pytest.raises(MCPClientError, match="tools capability"):
        await session.list_tools()

    await session.close()


@pytest.mark.asyncio
async def test_client_session_list_all_tools_follows_cursors() -> None:
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _HANDSHAKE_SERVER]),
    )

    await session.start()
    tools = await session.list_all_tools()

    assert [item["name"] for item in tools] == ["search", "fetch", "final"]

    await session.close()


@pytest.mark.asyncio
async def test_client_session_call_tool_returns_result_payload() -> None:
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _HANDSHAKE_SERVER]),
    )

    await session.start()
    result = await session.call_tool("search", {"query": "weather"})

    assert result["isError"] is False
    assert result["structuredContent"] == {"echo": {"query": "weather"}}
    assert result["content"][0]["text"] == "ok"

    await session.close()


@pytest.mark.asyncio
async def test_client_session_call_tool_surfaces_protocol_error() -> None:
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _HANDSHAKE_SERVER]),
    )

    await session.start()

    with pytest.raises(MCPClientError, match="tool failed"):
        await session.call_tool("explode")

    await session.close()


@pytest.mark.asyncio
async def test_client_session_initialize_and_request_over_http_transport(
    unused_tcp_port,
) -> None:
    state = {"initialized": False, "protocol_headers": []}

    async def handler(request: web.Request) -> web.Response:
        payload = await request.json()
        state["protocol_headers"].append(request.headers.get("MCP-Protocol-Version"))
        method = payload.get("method")
        if method == "initialize":
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "serverInfo": {"name": "http-mcp", "version": "1.0.0"},
                        "capabilities": {"tools": {"listChanged": True}},
                    },
                }
            )
        if method == "notifications/initialized":
            state["initialized"] = True
            return web.json_response({})
        if method == "tools/list":
            assert state["initialized"] is True
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {"tools": [{"name": "http-search"}]},
                }
            )
        if method == "tools/call":
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "content": [{"type": "text", "text": "http-ok"}],
                        "isError": False,
                    },
                }
            )
        raise AssertionError(f"unexpected method: {method}")

    app = web.Application()
    app.router.add_post("/mcp", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", unused_tcp_port)
    await site.start()

    session = MCPClientSession(
        _make_http_source(),
        MCPHTTPTransport(f"http://127.0.0.1:{unused_tcp_port}/mcp"),
    )

    await session.start()
    tools = await session.list_tools()
    call_result = await session.call_tool("http-search", {"q": "test"})

    assert session.state.initialized is True
    assert session.state.negotiation.server_info["name"] == "http-mcp"
    assert tools["tools"][0]["name"] == "http-search"
    assert call_result["content"][0]["text"] == "http-ok"
    assert state["protocol_headers"] == [
        None,
        "2025-03-26",
        "2025-03-26",
        "2025-03-26",
    ]

    await session.close()
    await runner.cleanup()


@pytest.mark.asyncio
async def test_client_session_request_with_timeout_sends_cancel_notification() -> None:
    transport = MCPStdioTransport(sys.executable, args=["-c", _TIMEOUT_SERVER])
    session = MCPClientSession(
        _make_source(),
        transport,
    )

    await session.start()

    with pytest.raises(MCPClientError, match="timed out"):
        await session.request_with_timeout("slow/method", timeout_s=0.01)

    await session.close()

    assert transport.stderr_lines
    assert '"requestId": 2' in transport.stderr_lines[-1]
    assert '"reason": "request timed out"' in transport.stderr_lines[-1]


@pytest.mark.asyncio
async def test_client_session_close_clears_negotiated_state() -> None:
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _HANDSHAKE_SERVER]),
    )

    await session.start()

    assert session.state.negotiation.protocol_version == "2025-03-26"

    await session.close()

    assert session.state.phase == "closed"
    assert session.state.initialized is False
    assert session.state.negotiation.protocol_version is None
    assert session.state.negotiation.server_info == {}
    assert session.state.negotiation.server_capabilities.tools is False


@pytest.mark.asyncio
async def test_client_session_dispatches_list_changed_notifications() -> None:
    notifications: list[str] = []
    notification_received = asyncio.Event()
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _LIST_CHANGED_SERVER]),
    )

    def _on_notification(notification) -> None:
        notifications.append(notification.method)
        if notification.method == "notifications/tools/list_changed":
            notification_received.set()

    session.add_notification_handler(_on_notification)

    await session.start()
    await asyncio.wait_for(notification_received.wait(), timeout=1.0)

    assert notifications == ["notifications/tools/list_changed"]

    await session.close()


@pytest.mark.asyncio
async def test_client_session_handles_roots_requests_and_notifications() -> None:
    transport = MCPStdioTransport(sys.executable, args=["-c", _ROOTS_SERVER])
    session = MCPClientSession(
        _make_source(),
        transport,
        client_capabilities=MCPClientCapabilities(
            roots=True,
            roots_list_changed=True,
        ),
        roots=[MCPRootDescriptor(uri="file:///initial", name="initial")],
    )

    await session.start()
    lines = await asyncio.wait_for(_wait_for_stderr_lines(transport, 1), timeout=1.0)
    first = [line for line in lines if '"kind": "roots"' in line][0]

    assert "file:///initial" in first

    changed = await session.update_roots(
        [MCPRootDescriptor(uri="file:///updated", name="updated")]
    )

    assert changed is True

    lines = await asyncio.wait_for(_wait_for_stderr_lines(transport, 3), timeout=1.0)
    assert any("notifications/roots/list_changed" in line for line in lines)
    assert any("file:///updated" in line for line in lines)

    unchanged = await session.update_roots(
        [MCPRootDescriptor(uri="file:///updated", name="updated")]
    )

    assert unchanged is False

    await session.close()


@pytest.mark.asyncio
async def test_client_session_close_is_idempotent_when_transport_close_fails() -> None:
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _HANDSHAKE_SERVER]),
    )

    await session.start()

    close_calls = {"count": 0}
    original_close = session.transport.close

    async def _failing_close() -> None:
        close_calls["count"] += 1
        await original_close()
        raise MCPTransportError("close failed")

    session.transport.close = _failing_close  # type: ignore[method-assign]

    with pytest.raises(MCPTransportError, match="close failed"):
        await session.close()

    assert session.state.phase == "closed"

    await session.close()

    assert close_calls["count"] == 1
    assert session.state.phase == "closed"


@pytest.mark.asyncio
async def test_client_session_lists_and_fetches_prompts_and_resources() -> None:
    session = MCPClientSession(
        _make_source(),
        MCPStdioTransport(sys.executable, args=["-c", _PROMPTS_RESOURCES_SERVER]),
    )

    await session.start()

    prompts = await session.list_all_prompts()
    prompt = await session.get_prompt("summarize", {"topic": "status"})
    resources = await session.list_all_resources()
    resource = await session.read_resource("file:///docs/readme.md")

    assert prompts[0]["name"] == "summarize"
    assert prompt["messages"][0]["content"][0]["text"] == "Prompt body"
    assert resources[0]["uri"] == "file:///docs/readme.md"
    assert resource["contents"][0]["text"] == "Resource body"

    await session.close()
