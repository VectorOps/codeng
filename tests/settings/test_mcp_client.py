from __future__ import annotations

import sys

import pytest

from vocode.mcp.client import MCPClientError
from vocode.mcp.client import MCPClientSession
from vocode.mcp.models import MCPClientCapabilities
from vocode.mcp.models import MCPSourceDescriptor
from vocode.mcp.models import MCPTransportKind
from vocode.mcp.transports import MCPStdioTransport


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


def _make_source() -> MCPSourceDescriptor:
    return MCPSourceDescriptor(
        source_name="local",
        transport=MCPTransportKind.stdio,
        scope="workflow",
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

    await session.close()


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
