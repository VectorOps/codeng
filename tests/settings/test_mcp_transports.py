from __future__ import annotations

import sys

import pytest

from vocode.mcp.protocol import MCPJSONRPCNotification
from vocode.mcp.protocol import MCPJSONRPCRequest
from vocode.mcp.protocol import MCPJSONRPCResponse
from vocode.mcp.transports import MCPStdioTransport
from vocode.mcp.transports import MCPTransportError


_ECHO_SERVER = """
import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('method') == 'initialize':
        sys.stderr.write('starting\\n')
        sys.stderr.flush()
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'id': msg['id'],
            'result': {
                'protocolVersion': '2025-03-26',
                'serverInfo': {'name': 'echo-server', 'version': '1.0.0'}
            }
        }) + '\\n')
        sys.stdout.flush()
    else:
        sys.stdout.write(json.dumps({
            'jsonrpc': '2.0',
            'method': 'notifications/initialized'
        }) + '\\n')
        sys.stdout.flush()
"""


@pytest.mark.asyncio
async def test_stdio_transport_sends_and_receives_jsonrpc_messages() -> None:
    transport = MCPStdioTransport(
        sys.executable,
        args=["-c", _ECHO_SERVER],
    )

    await transport.start()
    await transport.send(MCPJSONRPCRequest(id=1, method="initialize"))
    response = await transport.receive()

    assert isinstance(response, MCPJSONRPCResponse)
    assert response.id == 1
    assert response.result["protocolVersion"] == "2025-03-26"

    await transport.send(MCPJSONRPCNotification(method="notifications/initialized"))
    notification = await transport.receive()

    assert isinstance(notification, MCPJSONRPCNotification)
    assert notification.method == "notifications/initialized"

    await transport.close()


@pytest.mark.asyncio
async def test_stdio_transport_captures_stderr_lines() -> None:
    transport = MCPStdioTransport(
        sys.executable,
        args=["-c", _ECHO_SERVER],
    )

    await transport.start()
    await transport.send(MCPJSONRPCRequest(id=1, method="initialize"))
    await transport.receive()
    await transport.close()

    assert transport.stderr_lines == ["starting\n"]


@pytest.mark.asyncio
async def test_stdio_transport_rejects_send_before_start() -> None:
    transport = MCPStdioTransport(sys.executable, args=["-c", _ECHO_SERVER])

    with pytest.raises(MCPTransportError, match="not running"):
        await transport.send(MCPJSONRPCRequest(id=1, method="initialize"))


@pytest.mark.asyncio
async def test_stdio_transport_rejects_invalid_json() -> None:
    transport = MCPStdioTransport(
        sys.executable,
        args=["-c", "import sys; sys.stdout.write('not-json\\n'); sys.stdout.flush()"],
    )

    await transport.start()

    with pytest.raises(MCPTransportError, match="invalid JSON"):
        await transport.receive()

    await transport.close()
