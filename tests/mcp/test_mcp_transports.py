from __future__ import annotations

import json
import sys

import pytest
from aiohttp import web

from vocode.mcp import transports as mcp_transports
from vocode.mcp.protocol import MCPJSONRPCNotification
from vocode.mcp.protocol import MCPJSONRPCRequest
from vocode.mcp.protocol import MCPJSONRPCResponse
from vocode.mcp.transports import MCPHTTPTransport
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


@pytest.mark.asyncio
async def test_http_transport_posts_jsonrpc_and_injects_headers(
    unused_tcp_port,
) -> None:
    observed: dict[str, object] = {}

    async def handler(request: web.Request) -> web.Response:
        observed["authorization"] = request.headers.get("Authorization")
        observed["protocol_version"] = request.headers.get("MCP-Protocol-Version")
        observed["custom"] = request.headers.get("X-Test")
        observed["payload"] = await request.json()
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-03-26",
                },
            }
        )

    app = web.Application()
    app.router.add_post("/mcp", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = unused_tcp_port
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    transport = MCPHTTPTransport(
        f"http://127.0.0.1:{port}/mcp",
        headers={"X-Test": "yes"},
        auth_token="secret-token",
        protocol_version="2025-03-26",
    )
    await transport.start()

    response = await transport.request(MCPJSONRPCRequest(id=1, method="initialize"))

    assert isinstance(response, MCPJSONRPCResponse)
    assert response.result["protocolVersion"] == "2025-03-26"
    assert observed["authorization"] == "Bearer secret-token"
    assert observed["protocol_version"] == "2025-03-26"
    assert observed["custom"] == "yes"
    assert observed["payload"] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {},
    }

    await transport.close()
    await runner.cleanup()


@pytest.mark.asyncio
async def test_http_transport_logs_auth_challenge_resolution(
    monkeypatch: pytest.MonkeyPatch,
    unused_tcp_port,
) -> None:
    records = []
    monkeypatch.setattr(mcp_transports, "logger", _RecordedLogger(records))
    attempts = {"count": 0}

    async def handler(request: web.Request) -> web.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return web.Response(
                status=401,
                headers={"WWW-Authenticate": 'Bearer realm="mcp"'},
                text="unauthorized",
            )
        return web.json_response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-03-26",
                },
            }
        )

    async def auth_challenge_handler(
        status_code: int,
        www_authenticate: str | None,
        step_up_attempt: int,
    ) -> dict[str, str] | None:
        assert status_code == 401
        assert www_authenticate == 'Bearer realm="mcp"'
        assert step_up_attempt == 0
        return {"X-Auth-Retry": "1"}

    app = web.Application()
    app.router.add_post("/mcp", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = unused_tcp_port
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    transport = MCPHTTPTransport(
        f"http://127.0.0.1:{port}/mcp",
        auth_challenge_handler=auth_challenge_handler,
    )
    await transport.start()

    response = await transport.request(MCPJSONRPCRequest(id=1, method="initialize"))

    assert isinstance(response, MCPJSONRPCResponse)
    events = [event for _, event, _ in records]
    assert "MCP HTTP auth challenge received" in events
    assert "MCP HTTP auth challenge resolved" in events

    challenge_record = next(
        record for record in records if record[1] == "MCP HTTP auth challenge received"
    )
    assert challenge_record[2]["status_code"] == 401

    await transport.close()
    await runner.cleanup()


@pytest.mark.asyncio
async def test_http_transport_rejects_invalid_json_response(
    unused_tcp_port,
) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response(text="not-json", content_type="application/json")

    app = web.Application()
    app.router.add_post("/mcp", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = unused_tcp_port
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    transport = MCPHTTPTransport(f"http://127.0.0.1:{port}/mcp")
    await transport.start()

    with pytest.raises(MCPTransportError, match="invalid JSON"):
        await transport.request(MCPJSONRPCRequest(id=1, method="initialize"))

    await transport.close()
    await runner.cleanup()
