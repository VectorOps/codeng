import asyncio

import pytest

from vocode.mcp.protocol import MCPJSONRPCError
from vocode.mcp.protocol import MCPJSONRPCErrorResponse
from vocode.mcp.protocol import MCPJSONRPCNotification
from vocode.mcp.protocol import MCPJSONRPCRequest
from vocode.mcp.protocol import MCPJSONRPCResponse
from vocode.mcp.protocol import MCPProtocolClient
from vocode.mcp.protocol import MCPProtocolError


def test_create_request_requires_initialize_first() -> None:
    client = MCPProtocolClient()

    with pytest.raises(MCPProtocolError, match="initialize must be the first"):
        client.create_request("tools/list")


def test_create_initialize_request_tracks_state() -> None:
    client = MCPProtocolClient()

    request = client.create_request("initialize", {"clientInfo": {"name": "vocode"}})

    assert isinstance(request, MCPJSONRPCRequest)
    assert request.method == "initialize"
    assert request.id == 1
    assert client.state.initialize_request_sent is True


def test_initialized_notification_requires_successful_initialize() -> None:
    client = MCPProtocolClient()
    client.create_request("initialize")

    with pytest.raises(MCPProtocolError, match="successful initialize response"):
        client.build_initialized_notification()


@pytest.mark.asyncio
async def test_handle_initialize_response_updates_negotiation() -> None:
    client = MCPProtocolClient()
    request = client.create_request("initialize")
    future = client.register_pending(request)

    client.handle_response(
        MCPJSONRPCResponse(
            id=request.id,
            result={
                "protocolVersion": "2025-03-26",
                "serverInfo": {"name": "demo-server", "version": "1.0.0"},
            },
        )
    )

    result = await future
    notification = client.build_initialized_notification()

    assert result["protocolVersion"] == "2025-03-26"
    assert client.state.initialized is True
    assert client.state.negotiation.protocol_version == "2025-03-26"
    assert client.state.negotiation.server_info["name"] == "demo-server"
    assert isinstance(notification, MCPJSONRPCNotification)
    assert notification.method == "notifications/initialized"


@pytest.mark.asyncio
async def test_handle_error_response_fails_pending_request() -> None:
    client = MCPProtocolClient()
    request = client.create_request("initialize")
    future = client.register_pending(request)

    client.handle_response(
        MCPJSONRPCErrorResponse(
            id=request.id,
            error=MCPJSONRPCError(code=-32603, message="boom"),
        )
    )

    with pytest.raises(MCPProtocolError, match="boom"):
        await future


@pytest.mark.asyncio
async def test_wait_for_response_times_out_and_clears_pending() -> None:
    client = MCPProtocolClient()
    request = client.create_request("initialize")
    client.register_pending(request)

    with pytest.raises(MCPProtocolError, match="timed out"):
        await client.wait_for_response(request, timeout_s=0.01)

    assert client.pending_count() == 0


def test_unknown_response_id_raises_protocol_error() -> None:
    client = MCPProtocolClient()

    with pytest.raises(MCPProtocolError, match="unknown response id"):
        client.handle_response(MCPJSONRPCResponse(id=99, result={}))


def test_build_cancel_notification_includes_request_id_and_reason() -> None:
    client = MCPProtocolClient()

    notification = client.build_cancel_notification(7)

    assert notification.method == "notifications/cancelled"
    assert notification.params == {
        "requestId": 7,
        "reason": "request timed out",
    }


@pytest.mark.asyncio
async def test_drop_pending_removes_registered_request() -> None:
    client = MCPProtocolClient()
    request = client.create_request("initialize")
    client.register_pending(request)

    client.drop_pending(request.id)

    assert client.pending_count() == 0
